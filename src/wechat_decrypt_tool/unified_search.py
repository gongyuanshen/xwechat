"""Account-scoped literal search over existing message indexes and saved records."""
import json
import math
import sqlite3
import time
from contextlib import closing

from .attachment_service import attachment_key, extraction_source_matches, validate_extracted_text, ready_index
from .chat_helpers import _resolve_account_dir, _make_search_tokens, _match_tokens, _make_snippet, _build_fts_query
from .chat_search_index import get_chat_search_index_db_path
from .library_service import LibraryService


MESSAGE_FIELDS = ('content', 'title', 'url', 'quoteTitle', 'quoteContent', 'amount')


def _payload(body):
    value = json.loads(body)
    if not isinstance(value, dict) or not isinstance(value.get('id'), str) or not value['id']:
        raise ValueError('消息索引来源损坏：缺少消息定位')
    for key in MESSAGE_FIELDS:
        types = (str, int, float) if key == 'amount' else (str,)
        if key in value and type(value[key]) not in types:
            raise ValueError(f'消息索引字段损坏：{key}')
    return value


def _fields(payload):
    return {key: str(payload[key]) for key in MESSAGE_FIELDS if payload.get(key) is not None}


def _matched_fields(fields, tokens):
    return [key for key, text in fields.items() if any(token.lower() in text.lower() for token in tokens)]


def _message_item(row, payload, source, text, tokens, matched_fields):
    if any(payload[key] != row['username'] for key in ('username', 'conversationUsername') if key in payload):
        raise ValueError('消息索引来源损坏：会话身份不一致')
    if row['db_stem'] == 'anti_revoke':
        # This is the identity written by format_revoked_message_as_chat_item;
        # its original database/table may differ from the archive's meta row.
        if row['server_id']:
            source_id = payload.get('serverIdStr') or str(row['server_id'])
            if not isinstance(source_id, str) or not source_id.isdigit() or int(source_id) != row['server_id']:
                raise ValueError('消息索引来源损坏：撤回存档服务端标识不一致')
        elif payload.get('db') and payload.get('table'):
            source_id = f"{payload['db']}:{payload['table']}:{row['local_id']}"
        else:
            source_id = str(row['local_id'])
        expected = f"anti_revoke:{row['username']}:{source_id}"
        if row['table_name'] != 'revoked_messages' or payload.get('isRevoked') is not True:
            raise ValueError('消息索引来源损坏：撤回存档类型不一致')
    else:
        expected = f"{row['db_stem']}:{row['table_name']}:{row['local_id']}"
    if payload['id'] != expected:
        raise ValueError('消息索引来源损坏：消息定位与索引记录不一致')
    return {'id': payload['id'], 'source': source, 'title': payload.get('title') or payload.get('content', '')[:80] or row['render_type'],
            'snippet': _make_snippet(text, tokens, max_len=240), 'matched_fields': matched_fields,
            'timestamp': row['create_time'], 'username': row['username'], 'anchor': payload['id'],
            'conversation_name': payload.get('conversationName') or row['username'],
            'sender': row['sender_username'], 'sender_name': payload.get('senderDisplayName') or row['sender_username'],
            'is_revoked': payload.get('isRevoked', False), 'revoke_time': payload.get('revokeTime')}


def _time_scope(start_time, end_time):
    clauses = ['m.is_hidden=0', 'm.is_official=0']
    params = []
    for expression, value in (('m.create_time>=?', start_time), ('m.create_time<=?', end_time)):
        if value is not None:
            clauses.append(expression)
            params.append(value)
    return clauses, params


def _chat(db, tokens, offset, limit, start_time, end_time):
    clauses, params = _time_scope(start_time, end_time)
    scope = ' AND '.join(clauses)
    available = db.execute('SELECT count(*) FROM message_meta m WHERE ' + scope, params).fetchone()[0]
    # Punctuation-only terms cannot produce unicode61 candidates. This query
    # shape is selected up front; query failures never switch execution modes.
    candidate_terms = [token for token in tokens if any(char.isalnum() for char in token)]
    fts_query = _build_fts_query(' '.join(candidate_terms))
    mode = 'fts' if fts_query else 'literal_scan'
    if fts_query:
        clauses.append('message_fts MATCH ?')
        params.append(fts_query)
    rows = db.execute('SELECT m.*,f.payload_json FROM message_meta m JOIN message_fts f ON f.rowid=m.rowid WHERE '
        + ' AND '.join(clauses) + ' ORDER BY m.create_time DESC,m.sort_seq DESC,m.local_id DESC,m.rowid DESC', params)
    items = []
    matched = 0
    for row in rows:
        payload = _payload(row['payload_json'])
        fields = _fields(payload)
        text = '\n'.join(fields.values())
        if not _match_tokens(text, tokens):
            continue
        matched += 1
        if matched <= offset:
            continue
        items.append(_message_item(row, payload, 'chat', text, tokens, _matched_fields(fields, tokens)))
        if len(items) > limit:
            break
    return items[:limit], len(items) > limit, {'available': available, 'searched': available,
        'message': '搜索本地消息索引快照，排除隐藏会话和公众号；索引片段未重新核对原消息，更新后的消息可能尚未覆盖。'}, mode


def _attachments(db, store, account, source, tokens, offset, limit, start_time, end_time):
    clauses, params = _time_scope(start_time, end_time)
    clauses += ["m.render_type IN ('file','image')", "m.db_stem <> 'anti_revoke'"]
    db.execute('ATTACH DATABASE ? AS ai', (store.path.resolve().as_uri() + '?mode=ro',))
    db.create_function('attachment_key', 2, lambda username, anchor: attachment_key(account, username, anchor), deterministic=True)
    rows = db.execute("SELECT m.*,f.payload_json,e.body AS extraction_body,e.updated AS extraction_updated "
        "FROM message_meta m JOIN message_fts f ON f.rowid=m.rowid LEFT JOIN ai.records e "
        "ON e.kind='local_media_text' AND e.account=? AND e.id=attachment_key(m.username,json_extract(f.payload_json,'$.id')) WHERE "
        + ' AND '.join(clauses) + ' ORDER BY m.create_time DESC,m.sort_seq DESC,m.local_id DESC,m.rowid DESC', [account, *params])
    counts = dict.fromkeys(('complete', 'partial', 'no_text', 'not_extracted', 'stale', 'document_text', 'local_ocr'), 0)
    available = searched = matched = 0
    items = []
    method = {'documents': 'document_text', 'ocr': 'local_ocr'}[source]
    for row in rows:
        available += 1
        payload = _payload(row['payload_json'])
        if row['extraction_body'] is None:
            counts['not_extracted'] += 1
            continue
        cached = json.loads(row['extraction_body'])
        state = validate_extracted_text(cached)
        extraction_method = state['extraction_method']
        if not extraction_source_matches(extraction_method, cached.get('message_signature'), account, row['username'], payload):
            counts['stale'] += 1
            continue
        counts[extraction_method] += 1
        counts[state['status']] += 1
        if extraction_method != method or not cached['text'].strip():
            continue
        searched += 1
        if not _match_tokens(cached['text'], tokens):
            continue
        if offset <= matched < offset + limit:
            item = _message_item(row, payload, source, cached['text'], tokens, ['extracted_text'])
            item['extraction'] = {**{key: value for key, value in state.items() if key != 'text'}, 'updated': row['extraction_updated']}
            items.append(item)
        matched += 1
    return items, matched > offset + limit, {'available': available, 'searched': searched, 'counts': counts,
        'message': '范围为消息索引中的文件和图片，排除隐藏会话、公众号和仅撤回存档。仅搜索来源仍有效的已提取文字；普通文档字层不等于图片已识别，部分结果与未检测到文字均单独标明。'}, 'records_scan'


def _notes(store, account, tokens, offset, limit, start_time, end_time):
    scope, params = ["kind='library_item'", 'account=?'], [account]
    dates = []
    # HTTP timestamps denote whole seconds; saved items use time.time() and
    # therefore retain fractional seconds through the inclusive final second.
    for expression, value in (("json_extract(body,'$.updated_at')>=?", start_time),
                              ("json_extract(body,'$.updated_at')<?", end_time + 1 if end_time is not None else None)):
        if value is not None:
            dates.append(expression)
            params.append(value)
    if dates:
        # A malformed timestamp has no known date scope. Keep it in the read
        # so the shared record validation reports it instead of hiding it.
        scope.append("((json_type(body,'$.updated_at') IS NOT 'integer' AND json_type(body,'$.updated_at') IS NOT 'real') OR ("
                     + ' AND '.join(dates) + '))')
    with store.connection() as db:
        folders = {row['id']: LibraryService.decode(row, 'library_folder', account)['name'] for row in
            db.execute("SELECT id,body FROM records WHERE kind='library_folder' AND account=?", (account,))}
        rows = db.execute('SELECT id,body FROM records WHERE ' + ' AND '.join(scope) + ' ORDER BY updated DESC,id DESC', params).fetchall()
    available = matched = 0
    items = []
    for row in rows:
        record = LibraryService.decode(row, 'library_item', account)
        timestamp = record['updated_at']
        if (type(timestamp) not in (int, float) or not math.isfinite(timestamp)
                or not isinstance(record['folder_id'], str) or record['folder_id'] not in folders
                or not isinstance(folders[record['folder_id']], str)
                or not isinstance(record['kind'], str) or record['kind'] not in {'message', 'report', 'attachment', 'extracted_text'}
                or any(not isinstance(record[key], str) for key in ('title', 'content', 'notes'))
                or not isinstance(record['tags'], list) or any(not isinstance(tag, str) for tag in record['tags'])):
            raise ValueError(f'资料检索字段或资料夹来源损坏：{record["id"]}')
        available += 1
        fields = {key: record[key] for key in ('title', 'content', 'notes')}
        fields['tags'] = '\n'.join(record['tags'])
        text = '\n'.join(fields.values())
        if not _match_tokens(text, tokens):
            continue
        if offset <= matched < offset + limit:
            items.append({'id': record['id'], 'source': 'notes', 'title': record['title'],
                'snippet': _make_snippet(text, tokens, max_len=240), 'matched_fields': _matched_fields(fields, tokens),
                'timestamp': timestamp, 'library_item_id': record['id'], 'folder_id': record['folder_id'],
                'folder_name': folders[record['folder_id']], 'item_kind': record['kind']})
        matched += 1
    return items, matched > offset + limit, {'available': available, 'searched': available,
        'message': '搜索当前资料的标题、正文、备注和标签，包括已保存报告及文字副本；按资料更新时间筛选，不包含历史专题版本，不要求原聊天仍存在。'}, 'records_scan'


def search_source(store, account, *, source, q, offset=0, limit=20, start_time=None, end_time=None):
    """The HTTP boundary validates the query and holds the account/snapshot lease."""
    started = time.perf_counter()
    tokens = _make_search_tokens(q)
    result = {'account': account, 'query': q, 'source': source, 'status': 'ready', 'items': [],
              'has_more': False, 'next_offset': offset}
    if source == 'notes':
        items, has_more, coverage, mode = _notes(store, account, tokens, offset, limit, start_time, end_time)
    else:
        directory = _resolve_account_dir(account)
        index, status, message = ready_index(directory)
        if status != 'success':
            return {**result, 'status': status, 'coverage': {'available': None, 'searched': None, 'index': index, 'message': message},
                    'elapsed_ms': round((time.perf_counter() - started) * 1000, 3)}
        with closing(sqlite3.connect(get_chat_search_index_db_path(directory).resolve().as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            if source == 'chat':
                items, has_more, coverage, mode = _chat(db, tokens, offset, limit, start_time, end_time)
            else:
                items, has_more, coverage, mode = _attachments(db, store, account, source, tokens, offset, limit, start_time, end_time)
        coverage['index'] = index
    return {**result, 'items': items, 'has_more': has_more, 'next_offset': offset + len(items), 'coverage': coverage,
            'query_mode': mode, 'elapsed_ms': round((time.perf_counter() - started) * 1000, 3)}
