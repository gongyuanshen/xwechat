"""Local attachment browsing, authoritative resolution and explicit text extraction."""
import hashlib
import json
import mimetypes
import re
import sqlite3
from contextlib import closing, nullcontext
from pathlib import Path

from fastapi import HTTPException
from starlette.responses import FileResponse

from .account_workers import account_to_thread
from .chat_helpers import _resolve_account_dir, _load_contact_rows, _pick_display_name
from .chat_search_index import get_chat_search_index_db_path, get_chat_search_index_status, start_chat_search_index_build
from .snapshot_registry import resolve_account_database_dir
from .local_ocr import IMAGE_SUFFIXES, OcrUnavailableError


DOCUMENT_SUFFIXES = {'.txt', '.md', '.csv', '.pdf', '.docx', '.xlsx', '.pptx'}


def attachment_key(account, username, anchor):
    return hashlib.sha256(f'{account}:{username}:{anchor}'.encode()).hexdigest()


def extraction_state(body, *, kind='file', name=''):
    if body is not None:
        value = json.loads(body) if isinstance(body, str) else body
        if not isinstance(value, dict):
            raise ValueError('附件正文缓存损坏')
        if 'text' in value:
            if not isinstance(value['text'], str):
                raise ValueError('附件正文缓存损坏')
            excerpt, characters = value['text'][:600], len(value['text'])
        else:
            excerpt, characters = value.get('text_excerpt'), value.get('text_characters')
            if not isinstance(excerpt, str) or type(characters) is not int or characters < 0:
                raise ValueError('附件正文缓存损坏')
        status = value.get('status') if value.get('complete') is True else 'partial'
        if status not in {'partial', 'complete', 'no_text'}:
            raise ValueError('附件正文缓存状态损坏')
        return {'status': status, 'text_excerpt': excerpt, 'text_characters': characters, 'skipped_images': value.get('skipped_images', 0),
                'extraction_method': value.get('extraction_method', 'document_text'), 'ocr_units': value.get('ocr_units', 0),
                'runtime': value.get('ocr_runtime'), 'ocr_engine': value.get('ocr_engine'),
                'message': value.get('message', '') if status != 'partial' else '历史缓存可能只包含部分正文，请重新提取以确认完整范围。'}
    if kind == 'image' or (kind == 'file' and Path(name).suffix.lower() in IMAGE_SUFFIXES):
        return {'status': 'not_extracted', 'text_excerpt': '', 'text_characters': 0, 'message': '尚未识别图片文字。', 'skipped_images': 0}
    if kind != 'file' or Path(name).suffix.lower() not in DOCUMENT_SUFFIXES:
        return {'status': 'unsupported', 'text_excerpt': '', 'text_characters': 0, 'message': '此类型不支持本地文档正文提取。', 'skipped_images': 0}
    return {'status': 'not_extracted', 'text_excerpt': '', 'text_characters': 0, 'message': '尚未提取本地正文。', 'skipped_images': 0}


def extraction_source_matches(method, signature, account, username, message):
    from .ai.agent_tools import attachment_message_signature, normalize
    if method not in {'document_text', 'local_ocr'}:
        raise ValueError(f'附件正文缓存来源无效：{method}')
    # Released document-text caches predate source signatures. OCR must be
    # bound to the original message and its media identity.
    if signature is None and method == 'document_text':
        return True
    if not isinstance(signature, dict):
        raise ValueError('附件正文缓存缺少有效的原消息身份')
    return signature == attachment_message_signature(normalize(account, username, message))


def read_extracted_text(db, account, username, message):
    """Read the full, source-checked cache in the caller's account transaction."""
    row = db.execute("SELECT body,updated FROM records WHERE kind='local_media_text' AND id=? AND account=?",
                     (attachment_key(account, username, message['id']), account)).fetchone()
    if row is None:
        raise HTTPException(404, '尚无提取或识别结果，请先提取正文或识别图片文字。')
    cached = json.loads(row['body'])
    state = extraction_state(cached)
    if not isinstance(cached.get('text'), str):
        raise ValueError('附件正文缓存损坏，缺少完整文字')
    method = state['extraction_method']
    if not extraction_source_matches(method, cached.get('message_signature'), account, username, message):
        raise HTTPException(409, '原消息或附件来源已变化，请重新提取或识别后查看文字。')
    digest, version = cached.get('file_hash'), cached.get('version')
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest) or type(version) is not int or version < 1:
        raise ValueError('附件正文缓存损坏，输入摘要或版本无效')
    units = cached.get('ocr_units') if method == 'local_ocr' else cached.get('ocr_units', 0)
    engine = cached.get('ocr_engine')
    if type(units) is not int or units < 0 or (method == 'local_ocr' and (not isinstance(engine, str) or not engine)):
        raise ValueError('附件正文缓存损坏，OCR 来源无效')
    return {**state, 'text': cached['text'], 'complete': cached.get('complete') is True,
            'file_hash': digest, 'version': version, 'ocr_units': units, 'ocr_engine': engine,
            'cache_token': hashlib.sha256(row['body'].encode()).hexdigest(), 'updated': row['updated']}


def ready_index(account_dir):
    index = get_chat_search_index_status(account_dir, source='decrypted')['index']
    build = index.get('build', {})
    if not index.get('ready') and build.get('status') not in {'building', 'error'}:
        start_chat_search_index_build(account_dir, rebuild=bool(index.get('exists')), source='decrypted')
        index = get_chat_search_index_status(account_dir, source='decrypted')['index']
        build = index.get('build', {})
    if build.get('status') == 'error':
        return index, 'index_error', build['error']
    if not index.get('ready'):
        return index, 'index_building', '正在建立本地消息索引，完成后可浏览附件。'
    return index, 'success', ''


def browse_attachments(store, account, *, kind='all', q='', search_in='name', username='', sender='',
                       start_time=None, end_time=None, offset=0, limit=40, filters=False):
    def source_matches(method, signature, user, payload):
        return extraction_source_matches(method, json.loads(signature) if signature is not None else None,
                                         account, user, json.loads(payload))

    account_dir = _resolve_account_dir(account)
    index, status, message = ready_index(account_dir)
    result = {'status': status, 'account': account, 'items': [], 'has_more': False, 'next_offset': offset,
              'index': index, 'message': message, 'conversations': [], 'senders': []}
    if status != 'success':
        return result
    with closing(sqlite3.connect(get_chat_search_index_db_path(account_dir).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('ATTACH DATABASE ? AS ai', (store.path.resolve().as_uri() + '?mode=ro',))
        db.create_function('attachment_key', 2, lambda user, anchor: attachment_key(account, user, anchor), deterministic=True)
        db.create_function('attachment_source_matches', 4, source_matches, deterministic=True)
        where = ["m.render_type IN ('file','image','video')", "m.db_stem <> 'anti_revoke'"]
        params = []
        for field, value in (('m.render_type', kind if kind != 'all' else ''), ('m.username', username), ('m.sender_username', sender)):
            if value:
                where.append(field + '=?')
                params.append(value)
        if start_time is not None:
            where.append('m.create_time>=?')
            params.append(start_time)
        if end_time is not None:
            where.append('m.create_time<=?')
            params.append(end_time)
        scope = ' AND '.join(where)
        join = ("FROM message_meta m JOIN message_fts f ON f.rowid=m.rowid LEFT JOIN ai.records e "
                "ON e.kind='local_media_text' AND e.account=? AND e.id=attachment_key(m.username,json_extract(f.payload_json,'$.id')) "
                "AND attachment_source_matches(CASE WHEN json_type(e.body,'$.extraction_method') IS NULL THEN 'document_text' "
                "ELSE json_extract(e.body,'$.extraction_method') END,json_extract(e.body,'$.message_signature'),m.username,f.payload_json) ")
        if filters:
            rows = db.execute('SELECT DISTINCT m.username,m.sender_username FROM message_meta m WHERE ' + scope, params).fetchall()
            users = sorted({r['username'] for r in rows})
            senders = sorted({r['sender_username'] for r in rows if r['sender_username']})
            contacts = _load_contact_rows(resolve_account_database_dir(account_dir) / 'contact.db', list(set(users + senders)))
            return {**result, 'conversations': [{'value': u, 'label': _pick_display_name(contacts.get(u), u)} for u in users],
                    'senders': [{'value': u, 'label': _pick_display_name(contacts.get(u), u)} for u in senders]}
        coverage = db.execute("SELECT sum(m.render_type='file') AS total_files, sum(m.render_type='image') AS total_images, "
                              "sum(json_extract(e.body,'$.complete') IS 1) AS complete, "
                              "sum(e.body IS NOT NULL AND json_extract(e.body,'$.complete') IS NOT 1) AS partial "
                              + join + 'WHERE ' + scope + " AND m.render_type IN ('file','image')", [account, *params]).fetchone()
        result['coverage'] = {key: int(coverage[key] or 0) for key in ('total_files', 'total_images', 'complete', 'partial')}
        result['coverage']['message'] = '正文搜索仅覆盖已提取的文档文字和本地 OCR 结果；未提取或未识别的内容不在正文范围内。'
        if q:
            name_match = "instr(lower(coalesce(json_extract(f.payload_json,'$.title'),'')),lower(?))>0"
            body_match = "instr(lower(coalesce(json_extract(e.body,'$.text'),'')),lower(?))>0"
            predicates = [name_match] if search_in == 'name' else [body_match] if search_in == 'body' else [name_match, body_match]
            where.append('(' + ' OR '.join(predicates) + ')')
            params.extend([q] * len(predicates))
        extraction_projection = ("CASE WHEN e.body IS NULL THEN NULL ELSE json_object("
            "'text_excerpt',CASE WHEN json_type(e.body,'$.text')='text' THEN substr(json_extract(e.body,'$.text'),max(1,instr(lower(json_extract(e.body,'$.text')),lower(?))-100),600) END,"
            "'text_characters',length(json_extract(e.body,'$.text')), 'complete',json(CASE WHEN json_extract(e.body,'$.complete') IS 1 THEN 'true' ELSE 'false' END),"
            "'status',json_extract(e.body,'$.status'),'message',coalesce(json_extract(e.body,'$.message'),''),"
            "'extraction_method',coalesce(json_extract(e.body,'$.extraction_method'),'document_text'),'ocr_units',coalesce(json_extract(e.body,'$.ocr_units'),0),"
            "'ocr_runtime',json_extract(e.body,'$.ocr_runtime'),'ocr_engine',json_extract(e.body,'$.ocr_engine'),"
            "'skipped_images',coalesce(json_extract(e.body,'$.skipped_images'),0)) END AS extraction_body ")
        rows = db.execute('SELECT m.*,f.payload_json,' + extraction_projection + join + 'WHERE ' + ' AND '.join(where)
                          + ' ORDER BY m.create_time DESC,m.sort_seq DESC,m.local_id DESC,m.rowid DESC LIMIT ? OFFSET ?',
                          [q if search_in in {'body', 'all'} else '', account, *params, limit + 1, offset]).fetchall()
        result['has_more'] = len(rows) > limit
        rows = rows[:limit]
        contacts = _load_contact_rows(resolve_account_database_dir(account_dir) / 'contact.db',
                                     list({r['username'] for r in rows} | {r['sender_username'] for r in rows}))
        for row in rows:
            payload = json.loads(row['payload_json'])
            title = payload.get('title') or {'image': '图片', 'video': '视频', 'file': '文件'}[row['render_type']]
            result['items'].append({'id': payload['id'], 'username': row['username'],
                'conversation_name': _pick_display_name(contacts.get(row['username']), row['username']),
                'sender': row['sender_username'], 'sender_name': _pick_display_name(contacts.get(row['sender_username']), row['sender_username']),
                'create_time': row['create_time'], 'kind': row['render_type'], 'name': title, 'size': payload.get('fileSize'),
                'extraction': extraction_state(row['extraction_body'], kind=row['render_type'], name=title)})
        result['next_offset'] = offset + len(rows)
        return result


async def authoritative_message(request, account, source):
    from .routers.library import validate_message_source
    from .routers.chat import get_chat_messages_around
    account_dir = _resolve_account_dir(account)
    await account_to_thread(account_dir, validate_message_source, account, source)
    evidence = await get_chat_messages_around(request=request, username=source.username, anchor_id=source.anchor,
                                             account=account, before=0, after=0, source=None)
    messages = evidence['messages']
    if (evidence['account'] != account or evidence['username'] != source.username or len(messages) != 1
            or evidence['anchorIndex'] != 0 or messages[0]['id'] != evidence['anchorId']
            or evidence['anchorId'].casefold() != source.anchor.casefold()):
        raise HTTPException(422, '附件来源与原始消息不一致')
    message = messages[0]
    if message['renderType'] not in {'file', 'image', 'video'}:
        raise HTTPException(422, '该消息不是文件、图片或视频附件')
    return message


def safe_filename(name):
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .') or '附件'
    suffix = Path(clean).suffix
    return clean[:180 - len(suffix)] + suffix if len(clean) > 180 and len(suffix) < 180 else clean[:180]


async def resolve_attachment(request, account, username, message):
    from .routers import chat_media
    from .media_helpers import _resolve_account_db_storage_dir, _resolve_account_wxid_dir, _resolve_media_path_for_kind
    from .ai.agent_tools import local_request
    account_dir = _resolve_account_dir(account)
    kind = message['renderType']
    path, data = None, None
    if kind == 'file':
        if not message.get('fileMd5'):
            raise HTTPException(422, '该文件消息缺少附件定位信息，无法查找原附件。请刷新当前账号消息后重试；若仍缺失，请检查原消息数据。')
        path = await account_to_thread(account_dir, _resolve_media_path_for_kind, account_dir,
                                     kind='file', md5=message.get('fileMd5', ''), username=username, allow_fallback_scan=False)
        if path is None:
            source_dir = await account_to_thread(account_dir, _resolve_account_wxid_dir, account_dir)
            if source_dir is None:
                source_dir = await account_to_thread(account_dir, _resolve_account_db_storage_dir, account_dir)
            if source_dir is None:
                raise HTTPException(404, '当前账号没有可访问的微信附件来源目录，无法查找本地原附件。请检查该账号的资源来源；仅导入聊天记录不能补齐未包含的附件。')
            raise HTTPException(404, '本机找不到该文件附件。请先在电脑版微信中打开对应消息并下载原附件，再返回重试；若仍未找到，请刷新当前账号数据后重试。')
        media_type = mimetypes.guess_type(message.get('title') or str(path))[0] or 'application/octet-stream'
    else:
        options = {'account': account, 'username': username, 'src_create_time': message['createTime'],
                   'server_id': message.get('serverId'), 'file_size': message.get('fileSize')}
        if kind == 'image':
            response = await chat_media.get_chat_image(local_request(), md5=message.get('imageMd5'), file_id=message.get('imageFileId'), **options)
        else:
            response = await chat_media.get_chat_video(md5=message.get('videoMd5'), file_id=message.get('videoFileId'), **options)
        try:
            if isinstance(response, FileResponse):
                path = Path(response.path)
            else:
                if response.status_code != 200:
                    raise HTTPException(response.status_code, '无法读取原始附件')
                data = response.body
            media_type = response.media_type or response.headers['content-type'].split(';')[0]
        finally:
            # Reused media routes may already reserve a file response. The outer
            # account work owns the path until our new response/copy takes over.
            lease = getattr(response, '_account_lease', None)
            if lease is not None:
                lease.release()
    ext = mimetypes.guess_extension(media_type) or ''
    name = message.get('title') if kind == 'file' else f'{kind}_{message["localId"]}{ext}'
    return {'path': Path(path) if path is not None else None, 'data': data, 'kind': kind,
            'name': safe_filename(name or '附件'), 'media_type': media_type,
            'preservation_note': '保存当前本地可解码图片，可能为缩略图，不保证原图。' if kind == 'image' else '已保存本地附件副本。'}


def extract_document(ai, account, username, message, resolved, *, ocr=False):
    from .ai.agent_tools import attachment_message_signature, normalize
    from .ai.media import iter_document
    from .local_ocr import recognize_image, ocr_job
    suffix = Path(resolved['name']).suffix.lower()
    kind = message['renderType']
    if ocr and not (kind == 'image' or kind == 'file' and suffix in IMAGE_SUFFIXES | {'.pdf'}):
        raise HTTPException(422, '本地 OCR 支持图片消息、静态 PNG/JPEG/WebP/BMP/TIFF 文件和 PDF。')
    if not ocr and (kind != 'file' or suffix not in DOCUMENT_SUFFIXES):
        raise HTTPException(422, '本地正文提取支持 TXT、MD、CSV、PDF、DOCX、XLSX 和 PPTX。')
    path = resolved['path']
    size = path.stat().st_size if path is not None else len(resolved['data'])
    if size > 200 * 1024 * 1024:
        raise HTTPException(413, '附件超过本地解析的 200 MB 单文件上限。')
    data = path.read_bytes() if path is not None else resolved['data']
    text, skipped, ocr_units = [], 0, 0
    parts = ({'label': '图片', 'image': data},) if kind == 'image' or suffix in IMAGE_SUFFIXES else iter_document(data, suffix, include_images=ocr, raw_images=ocr)
    try:
        with (ocr_job() if ocr else nullcontext()) as job:
            for part in parts:
                if part.get('skipped_image'):
                    skipped += 1
                elif 'image' in part:
                    recognized = recognize_image(part['image'])
                    ocr_units += 1
                    if recognized.strip():
                        text.append(f'[{part["label"]} · 本地 OCR] {recognized}')
                elif part.get('text', '').strip():
                    text.append(f'[{part["label"]}] {part["text"]}')
    except OcrUnavailableError as error:
        raise HTTPException(503, str(error)) from error
    except Exception as error:
        raise HTTPException(422, f'{"本地 OCR" if ocr else "本地文档解析"}失败：{error}') from error
    finally:
        if kind == 'file' and suffix not in IMAGE_SUFFIXES:
            parts.close()
    body = {'text': '\n'.join(text), 'username': username, 'anchor': message['id'],
            'file_hash': hashlib.sha256(data).hexdigest(), 'version': 2, 'complete': True,
            'message_signature': attachment_message_signature(normalize(account, username, message)),
            'extraction_method': 'local_ocr' if ocr else 'document_text', 'ocr_units': ocr_units,
            'ocr_engine': job.engine_name if ocr else None,
            'ocr_runtime': {**job.metadata(), 'active': False} if ocr else None,
            'status': 'complete' if text else 'no_text', 'skipped_images': skipped,
            'message': ('已提取本地文字，内嵌图片未识别。' if skipped else '已提取完整本地文字。') if text else '文件没有可提取文字；扫描图片未执行 OCR。'}
    if ocr:
        body['message'] = (f'已提取文字并识别 {ocr_units} 个图片部件；OCR 结果请核对原图。'
                           if text else f'已识别 {ocr_units} 个图片部件，未检测到文字；不代表图片一定没有文字。')
    # Publish only after parsing completes; interruption/error never exposes a
    # partly parsed document as a complete extraction.
    from .library_service import LibraryService
    from .routers.ai import account_name
    with LibraryService(ai, account_name).transaction(account) as (db, owner):
        import time
        key = attachment_key(owner, username, message['id'])
        if not ocr:
            previous = db.execute("SELECT body FROM records WHERE kind='local_media_text' AND id=? AND account=?", (key, owner)).fetchone()
            if previous is not None:
                previous = json.loads(previous[0])
                # A plain parser cannot replace an OCR result for the same
                # bytes with a less complete text layer. Changed files replace it.
                if (previous.get('extraction_method') == 'local_ocr' and previous.get('complete') is True
                        and previous['file_hash'] == body['file_hash'] and previous['message_signature'] == body['message_signature']):
                    return extraction_state(previous)
        db.execute('INSERT INTO records VALUES(?,?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body,updated=excluded.updated',
                   ('local_media_text', key, owner, json.dumps(body, ensure_ascii=False), time.time()))
    return extraction_state(body)
