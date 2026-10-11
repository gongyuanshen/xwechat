"""Account-scoped local collections in the existing AI records database."""
import json
import hashlib
import shutil
import time
import uuid
from contextlib import contextmanager

from fastapi import HTTPException
from .account_workers import AccountFileResponse


_attachment_downloads = {}


class LibraryFileResponse(AccountFileResponse):
    """Reserve a saved copy until its last response read, without a DB transaction."""
    def __init__(self, path, *, store, **kwargs):
        self.store, self.copy_key = store, str(path.resolve())
        with store.lock:
            super().__init__(path, **kwargs)
            _attachment_downloads[self.copy_key] = _attachment_downloads.get(self.copy_key, 0) + 1

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with self.store.lock:
                _attachment_downloads[self.copy_key] -= 1
                if not _attachment_downloads[self.copy_key]:
                    del _attachment_downloads[self.copy_key]


class LibraryService:
    def __init__(self, ai, resolve_account):
        self.ai = ai
        self.store = ai.store
        self.resolve_account = resolve_account

    @contextmanager
    def transaction(self, account):
        with self.ai.account_lifecycle_lock:
            # Resolve while deletion is excluded: a missing account must never
            # reactivate writes, while a genuinely reimported snapshot may do so.
            owner = self.resolve_account(account)
            self.ai.deleted_accounts.discard(owner)
            self.store.revoked_accounts.discard(owner)
            with self.store.connection() as db:
                yield db, owner

    @staticmethod
    def decode(row, kind, account):
        value = json.loads(row['body'])
        required = {'id', 'account', 'created_at', 'updated_at'} | {
            'library_folder': {'name'}, 'library_search': {'name', 'criteria'},
            'library_item': {'kind', 'folder_id', 'title', 'content', 'source', 'report', 'notes', 'tags', 'verified'},
        }[kind]
        if not isinstance(value, dict) or not required.issubset(value) or value['account'] != account or value['id'] != row['id']:
            raise ValueError(f'资料记录损坏: {kind}/{row["id"]}')
        if kind == 'library_item' and value['kind'] == 'extracted_text':
            extraction, source = value.get('extraction'), value['source']
            if (not isinstance(extraction, dict) or not {'status', 'complete', 'extraction_method', 'ocr_units',
                    'file_hash', 'version', 'ocr_engine', 'cache_token', 'updated'}.issubset(extraction)
                    or not isinstance(source, dict) or not {'username', 'anchor', 'text', 'sender', 'create_time', 'render_type'}.issubset(source)
                    or not isinstance(value['content'], str)):
                raise ValueError(f'提取文字资料来源损坏: {row["id"]}')
        return value

    def get(self, db, kind, id, account):
        row = db.execute('SELECT id,body FROM records WHERE kind=? AND id=? AND account=?', (kind, id, account)).fetchone()
        if row is None:
            raise HTTPException(404, '资料记录不存在')
        return self.decode(row, kind, account)

    def list(self, kind, account, folder_id=None):
        with self.store.connection() as db:
            if folder_id is not None:
                self.get(db, 'library_folder', folder_id, account)
            rows = db.execute('SELECT id,body FROM records WHERE kind=? AND account=? ORDER BY updated DESC', (kind, account)).fetchall()
            records = [self.decode(row, kind, account) for row in rows]
            return [r for r in records if folder_id is None or r['folder_id'] == folder_id]

    @staticmethod
    def put(db, kind, record):
        db.execute('INSERT INTO records VALUES(?,?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body,updated=excluded.updated',
                   (kind, record['id'], record['account'], json.dumps(record, ensure_ascii=False), record['updated_at']))
        return record

    def create(self, kind, account, fields):
        with self.transaction(account) as (db, account):
            if kind == 'library_item':
                self.get(db, 'library_folder', fields['folder_id'], account)
                if fields['kind'] == 'report':
                    report = fields['report']
                    row = db.execute("SELECT body FROM records WHERE kind='agent_run' AND id=? AND account=?", (report['id'], account)).fetchone()
                    if row is None:
                        raise HTTPException(404, '分析任务不存在')
                    run = json.loads(row['body'])
                    if run['status'] != 'completed' or run['answer'] != report['answer'] or run.get('version') != report.get('version'):
                        raise HTTPException(409, '分析任务已变化，请重新保存')
            now = time.time()
            return self.put(db, kind, {**fields, 'id': uuid.uuid4().hex, 'account': account, 'created_at': now, 'updated_at': now})

    def create_extracted_text(self, account, fields, message, cache_token):
        from .attachment_service import read_extracted_text
        with self.transaction(account) as (db, owner):
            self.get(db, 'library_folder', fields['folder_id'], owner)
            cached = read_extracted_text(db, owner, fields['source']['username'], message)
            if cached['cache_token'] != cache_token:
                raise HTTPException(409, '提取文字已变化，请重新打开全文后保存。')
            if not cached['complete'] or not cached['text'].strip():
                raise HTTPException(422, '只能保存已完整提取且有文字的结果；请先重新提取或识别。')
            extraction = {key: cached[key] for key in ('status', 'complete', 'extraction_method', 'ocr_units',
                          'file_hash', 'version', 'ocr_engine', 'cache_token', 'updated')}
            now = time.time()
            return self.put(db, 'library_item', {**fields, 'content': cached['text'], 'extraction': extraction,
                'title': fields['title'] or ('图片识别文字' if cached['extraction_method'] == 'local_ocr' else '附件提取正文'),
                'id': uuid.uuid4().hex, 'account': owner, 'created_at': now, 'updated_at': now})

    def update(self, kind, id, account, fields):
        with self.transaction(account) as (db, account):
            record = self.get(db, kind, id, account)
            if kind == 'library_item':
                if 'content' in fields and record['kind'] != 'report':
                    detail = '提取文字不能修改；可在笔记中记录人工更正。' if record['kind'] == 'extracted_text' else '原始消息内容不能修改'
                    raise HTTPException(422, detail)
                if 'content' in fields:
                    fields = {**fields, 'user_edited': fields['content'] != record['report']['answer']}
                if 'folder_id' in fields:
                    self.get(db, 'library_folder', fields['folder_id'], account)
            return self.put(db, kind, {**record, **fields, 'updated_at': time.time()})

    def attachment_path(self, account, id):
        import re
        if not re.fullmatch('[0-9a-f]{32}', id):
            raise ValueError('附件副本编号无效')
        root = self.store.root.resolve()
        path = root / 'library' / hashlib.sha256(account.encode()).hexdigest() / id / 'content'
        if not path.resolve().is_relative_to(root):
            raise ValueError('附件副本路径不属于本地资料库')
        return path

    def create_attachment(self, account, fields, resolved):
        # Copy outside the short records transaction, so large videos do not
        # block Agent writes. The caller holds an account work reservation.
        with self.store.connection() as db:
            self.get(db, 'library_folder', fields['folder_id'], account)
        id = uuid.uuid4().hex
        path = self.attachment_path(account, id)
        path.parent.mkdir(parents=True)
        try:
            source = resolved['path']
            before = source.stat() if source is not None else None
            size = before.st_size if before is not None else len(resolved['data'])
            if shutil.disk_usage(path.parent).free < size:
                raise HTTPException(507, '本地磁盘空间不足，无法保存附件副本。')
            digest, copied = hashlib.sha256(), 0
            with path.open('xb') as target:
                if source is not None:
                    with source.open('rb') as reader:
                        while chunk := reader.read(1024 * 1024):
                            target.write(chunk)
                            digest.update(chunk)
                            copied += len(chunk)
                    after = source.stat()
                    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino) or copied != size:
                        raise HTTPException(409, '附件在复制期间发生变化，请重新保存。')
                else:
                    target.write(resolved['data'])
                    digest.update(resolved['data'])
                    copied = size
            metadata = {key: resolved[key] for key in ('name', 'kind', 'media_type', 'preservation_note')}
            metadata.update(size=copied, sha256=digest.hexdigest())
            with self.transaction(account) as (db, owner):
                self.get(db, 'library_folder', fields['folder_id'], owner)
                now = time.time()
                item = {**fields, 'id': id, 'account': owner, 'attachment': metadata,
                        'created_at': now, 'updated_at': now}
                self.put(db, 'library_item', item)
            return item
        except BaseException:
            path.unlink(missing_ok=True)
            path.parent.rmdir()
            raise

    def delete(self, kind, id, account):
        copies = []
        with self.transaction(account) as (db, account):
            record = self.get(db, kind, id, account)
            if kind == 'library_folder':
                copies = [row['id'] for row in db.execute("SELECT id FROM records WHERE kind='library_item' AND account=? "
                    "AND json_extract(body,'$.folder_id')=? AND json_extract(body,'$.kind')='attachment'", (account, id))]
            elif kind == 'library_item' and record['kind'] == 'attachment':
                copies = [id]
            if any(_attachment_downloads.get(str(self.attachment_path(account, copy).resolve()), 0) for copy in copies):
                raise HTTPException(409, '附件正在下载或预览，请结束后再删除；资料记录和副本均已保留。')
            if kind == 'library_folder':
                db.execute("DELETE FROM records WHERE kind='library_item' AND account=? AND json_extract(body,'$.folder_id')=?", (account, id))
            db.execute('DELETE FROM records WHERE kind=? AND id=? AND account=?', (kind, id, account))
        # Commit record deletion first. A filesystem failure may retain an
        # unreferenced copy, but never leaves a live record pointing at no file.
        try:
            for copy in copies:
                path = self.attachment_path(account, copy)
                path.unlink(missing_ok=True)
                if path.parent.exists():
                    path.parent.rmdir()
        except OSError as error:
            raise HTTPException(500, {'code': 'library_cleanup_failed', 'record_deleted': True,
                'message': f'资料记录已删除，但附件副本清理失败：{error}'}) from error
        return {'status': 'success'}
