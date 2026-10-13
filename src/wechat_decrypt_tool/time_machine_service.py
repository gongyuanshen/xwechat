"""Date comparisons over the existing, explicitly versioned local message index."""
from contextlib import closing
from datetime import date, datetime, time, timedelta
import hashlib
import json
import sqlite3

from fastapi import HTTPException

from .attachment_service import ready_index
from .chat_helpers import _load_contact_rows, _pick_display_name, _resolve_account_dir
from .chat_search_index import get_chat_search_index_db_path, start_chat_search_index_build
from .snapshot_registry import resolve_account_database_dir


VISIBLE = 'm.is_hidden=0 AND m.is_official=0'


def date_window(start: date, end: date):
    if start > end:
        raise HTTPException(422, '开始日期不能晚于结束日期')
    if end == date.max:
        raise HTTPException(422, '结束日期必须早于 9999-12-31')
    try:
        return (int(datetime.combine(start, time.min).timestamp()),
                int(datetime.combine(end + timedelta(days=1), time.min).timestamp()))
    except (ValueError, OverflowError, OSError) as error:
        raise HTTPException(422, '此电脑不支持所选日期，请选择可读取的本地日期范围。') from error


def _index_context(account):
    account_dir = _resolve_account_dir(account)
    index, status, message = ready_index(account_dir)
    if status != 'success':
        if status == 'index_building':
            message = '正在建立本地消息索引，完成后请点击“开始回看”。首次建立可能需要一些时间。'
        raise HTTPException(409 if status == 'index_building' else 503,
                            {'code': status, 'message': message})
    return account_dir, index


def _revision(db):
    metadata = dict(db.execute('SELECT key,value FROM meta'))
    # These values belong to the open SQLite file, so an atomic index rebuild
    # cannot mix the catalog with another generation during pagination.
    return hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest(), int(metadata['built_at'])


def catalog(account, username=''):
    account_dir, index = _index_context(account)
    with closing(sqlite3.connect(get_chat_search_index_db_path(account_dir).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        revision, built_at = _revision(db)
        where, params = VISIBLE, []
        if username:
            where += ' AND m.username=?'
            params.append(username)
        first = db.execute('SELECT m.create_time FROM message_meta m WHERE ' + where
                           + ' ORDER BY m.create_time ASC LIMIT 1', params).fetchone()
        last = db.execute('SELECT m.create_time FROM message_meta m WHERE ' + where
                          + ' ORDER BY m.create_time DESC LIMIT 1', params).fetchone()
        years = []
        if first is not None:
            for year in range(datetime.fromtimestamp(last[0]).year, datetime.fromtimestamp(first[0]).year - 1, -1):
                start, end = date_window(date(year, 1, 1), date(year, 12, 31))
                if db.execute('SELECT 1 FROM message_meta m WHERE ' + where
                              + ' AND m.create_time>=? AND m.create_time<? LIMIT 1', [*params, start, end]).fetchone():
                    years.append(year)
        usernames = [row[0] for row in db.execute('SELECT DISTINCT m.username FROM message_meta m WHERE ' + VISIBLE)]
        contacts = _load_contact_rows(resolve_account_database_dir(account_dir) / 'contact.db', usernames)
        conversations = [{'username': user, 'name': _pick_display_name(contacts.get(user), user)} for user in usernames]
    return {'account': account, 'revision': revision, 'built_at': built_at, 'up_to_date': index['upToDate'],
            'source': 'decrypted_index', 'timezone': datetime.now().astimezone().tzname(), 'years': years,
            'conversations': sorted(conversations, key=lambda item: (item['name'], item['username']))}


def messages(account, *, start: date, end: date, revision: str, username='', offset=0, limit=30):
    start_ts, end_ts = date_window(start, end)
    account_dir, index = _index_context(account)
    with closing(sqlite3.connect(get_chat_search_index_db_path(account_dir).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        current_revision, built_at = _revision(db)
        if current_revision != revision:
            raise HTTPException(409, {'code': 'time_machine_index_changed', 'message': '本地消息索引已更新，请重新回看，避免混用不同版本。'})
        db.row_factory = sqlite3.Row
        where = [VISIBLE, 'm.create_time>=?', 'm.create_time<?']
        params = [start_ts, end_ts]
        if username:
            where.append('m.username=?')
            params.append(username)
        rows = db.execute('SELECT m.username,m.sender_username,m.create_time,m.render_type,f.payload_json '
                          'FROM message_meta m JOIN message_fts f ON f.rowid=m.rowid WHERE ' + ' AND '.join(where)
                          + ' ORDER BY m.create_time ASC,m.sort_seq ASC,m.local_id ASC,m.rowid ASC LIMIT ? OFFSET ?',
                          [*params, limit + 1, offset]).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        contacts = _load_contact_rows(resolve_account_database_dir(account_dir) / 'contact.db',
                                     list({r['username'] for r in rows} | {r['sender_username'] for r in rows}))
        items = []
        for row in rows:
            payload = json.loads(row['payload_json'])
            if not isinstance(payload, dict) or not isinstance(payload.get('id'), str) or not payload['id']:
                raise ValueError('时光机消息索引缺少原消息定位信息')
            if not isinstance(payload.get('content'), str):
                raise ValueError('时光机消息索引正文格式错误')
            items.append({'id': payload['id'], 'username': row['username'],
                          'conversation_name': _pick_display_name(contacts.get(row['username']), row['username']),
                          'sender_name': _pick_display_name(contacts.get(row['sender_username']), row['sender_username']),
                          'create_time': row['create_time'], 'kind': row['render_type'],
                          'content': payload['content'], 'title': payload.get('title', ''),
                          'quote_content': payload.get('quoteContent', ''), 'is_sent': bool(payload.get('isSent')),
                          'is_revoked': bool(payload.get('isRevoked')), 'revoke_time': payload.get('revokeTime')})
    return {'account': account, 'revision': current_revision, 'built_at': built_at, 'up_to_date': index['upToDate'],
            'source': 'decrypted_index', 'items': items, 'has_more': has_more,
            'next_offset': offset + len(items) if has_more else None}


def refresh_index(account):
    return start_chat_search_index_build(_resolve_account_dir(account), rebuild=True, source='decrypted')
