"""Portable, account-scoped application data and verified isolated restoration."""
from __future__ import annotations

import hashlib
import json
import math
import os
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from typing import BinaryIO
import uuid
import zipfile

from .ai.storage import SCHEMA_SQL
from .archive_checksums import (
    ArchiveChecksumError, _unique_json_object, validate_zip_checksums, write_zip_checksums,
)
from .library_service import LibraryService, _attachment_downloads
from .matter_service import MatterService
from .snapshot_registry import SnapshotRegistryError, _check_ancestors


KINDS = ('matter', 'library_folder', 'library_item', 'library_version', 'library_search')
_COUNT_KEYS = dict(zip(KINDS, ('matters', 'folders', 'items', 'versions', 'searches')))
_FORMAT = 'xwechat-application-backup'
_CHUNK = 1024 * 1024


class ApplicationBackupError(ValueError):
    """A backup cannot preserve or restore the declared application data."""


class ApplicationBackupConflict(ApplicationBackupError):
    """Local data changed or cannot be retained without breaking restored data."""


def _require(condition, message):
    if not condition:
        raise ApplicationBackupError(message)


def _json(value):
    def invalid_constant(value):
        raise ApplicationBackupError(f'备份 JSON 包含无效数字：{value}')
    try:
        return json.loads(value, object_pairs_hook=_unique_json_object, parse_constant=invalid_constant)
    except (ValueError, UnicodeError) as error:
        raise ApplicationBackupError(f'备份 JSON 无效：{error}') from error


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _number(value):
    return (type(value) is int and 0 <= value < 2**63
            or type(value) is float and math.isfinite(value) and value >= 0)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _timestamp(value):
    if not _number(value):
        return False
    try:
        datetime.fromtimestamp(value, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return False
    return True


def _plain_path(path):
    try:
        _check_ancestors(path)
    except SnapshotRegistryError as error:
        raise ApplicationBackupError(str(error)) from error


def _validate_manifest(manifest):
    """Validate untrusted records and references once, before restoring any data."""
    _require(isinstance(manifest, dict) and set(manifest) ==
             {'format', 'version', 'account', 'records', 'reminders'}, '应用资料备份清单结构无效')
    _require(manifest['format'] == _FORMAT and type(manifest['version']) is int and manifest['version'] == 1,
             '不支持此应用资料备份格式或版本')
    account = manifest['account']
    _require(_text(account) and account == account.strip() and not any(ord(c) < 32 for c in account), '备份账号标识无效')
    _require(isinstance(manifest['records'], list) and isinstance(manifest['reminders'], list), '备份记录列表无效')
    decoded = {kind: {} for kind in KINDS}
    for row in manifest['records']:
        _require(isinstance(row, dict) and set(row) == {'kind', 'id', 'account', 'body', 'updated'}, '备份数据行结构无效')
        kind = row['kind']
        _require(isinstance(kind, str) and kind in decoded, '备份包含不支持的记录类型')
        _require(_text(row['id']) and row['id'] not in decoded[kind] and row['account'] == account
                 and isinstance(row['body'], str) and _number(row['updated']), '备份记录编号、账号或更新时间无效')
        body = _json(row['body'])
        try:
            if kind == 'matter':
                MatterService.decode(row)
            else:
                LibraryService.decode(row, kind, account)
        except (ValueError, TypeError, KeyError) as error:
            raise ApplicationBackupError(f'备份记录结构损坏：{kind}/{row["id"]}: {error}') from error
        _require(_number(body['created_at']) and _number(body['updated_at']), '备份记录时间无效')
        decoded[kind][row['id']] = body

    folders = decoded['library_folder']
    for folder in folders.values():
        _require(_text(folder['name']), '资料夹名称无效')
    for search in decoded['library_search'].values():
        # Reuse the existing search input contract instead of maintaining another schema.
        from .routers.library import Criteria
        _require(_text(search['name']), '保存搜索名称无效')
        try:
            Criteria.model_validate(search['criteria'])
        except ValueError as error:
            raise ApplicationBackupError(f'保存搜索条件无效：{error}') from error

    def validate_source(source):
        _require(isinstance(source, dict) and {'username', 'anchor', 'text', 'sender', 'create_time', 'render_type'}.issubset(source),
                 '保存的原消息来源字段缺失')
        _require(_text(source['username']) and _text(source['anchor']) and _text(source['render_type'])
                 and isinstance(source['text'], str) and isinstance(source['sender'], str)
                 and type(source['create_time']) is int and source['create_time'] >= 0,
                 '保存的原消息来源字段类型无效')

    attachments = {}
    def validate_item(item, folder_id=None):
        _require(isinstance(item['folder_id'], str) and item['folder_id'] in folders
                 and (folder_id is None or item['folder_id'] == folder_id), '资料引用了不存在或不一致的资料夹')
        _require(isinstance(item['kind'], str) and item['kind'] in {'message', 'report', 'attachment', 'extracted_text'}
                 and isinstance(item['title'], str) and isinstance(item['content'], str)
                 and isinstance(item['notes'], str) and isinstance(item['tags'], list)
                 and all(isinstance(tag, str) for tag in item['tags']) and type(item['verified']) is bool
                 and _number(item['created_at']) and _number(item['updated_at']),
                 '资料内容、笔记或标签结构无效')
        if item['kind'] == 'report':
            report = item['report']
            _require(isinstance(report, dict) and _text(report.get('id')) and isinstance(report.get('answer'), str)
                     and isinstance(report.get('model_metadata'), dict)
                     and all(isinstance(model, dict) for model in report['model_metadata'].values()) and item['source'] is None,
                     '保存报告缺少原始回答或模型元数据')
            citation_ids = set()
            for key in ('citations', 'evidence'):
                if key not in report:
                    continue
                _require(isinstance(report[key], list), '保存报告的引用证据列表无效')
                ids = []
                for entry in report[key]:
                    _require(isinstance(entry, dict) and _text(entry.get('source')), '保存报告的引用证据编号无效')
                    ids.append(entry['source'])
                _require(len(set(ids)) == len(ids), '保存报告包含重复引用证据编号')
                if key == 'citations':
                    citation_ids = set(ids)
                else:
                    _require(set(ids) == citation_ids, '保存报告的原始证据与引用编号不一致')
            if 'coverage_warnings' in report:
                _require(isinstance(report['coverage_warnings'], list)
                         and all(isinstance(warning, str) for warning in report['coverage_warnings']), '保存报告的覆盖提醒无效')
            for key in ('created', 'started_at', 'finished_at'):
                if key in report and report[key] is not None:
                    _require(_timestamp(report[key]), '保存报告的时间无效')
            if 'references' in report:
                references = report['references']
                _require(isinstance(references, (list, dict)), '保存报告的引用列表无效')
                for ref in references.values() if isinstance(references, dict) else references:
                    _require(isinstance(ref, dict), '保存报告的引用条目无效')
                    for key in ('id', 'name', 'label', 'kind'):
                        if key in ref:
                            _require(isinstance(ref[key], str), '保存报告的引用字段无效')
                    if 'sources' in ref:
                        _require(isinstance(ref['sources'], list) and all(isinstance(value, str) for value in ref['sources']),
                                 '保存报告的引用来源列表无效')
        else:
            validate_source(item['source'])
            _require(item['report'] is None, '原文资料包含不一致的报告来源')
        if item['kind'] == 'extracted_text':
            extraction = item['extraction']
            _require(isinstance(extraction['status'], str) and type(extraction['complete']) is bool
                     and isinstance(extraction['extraction_method'], str)
                     and extraction['extraction_method'] in {'local_ocr', 'document_text'}
                     and type(extraction['version']) is int and extraction['version'] >= 1
                     and type(extraction['ocr_units']) is int and extraction['ocr_units'] >= 0
                     and _timestamp(extraction['updated']), '保存的文字提取元数据无效')
            for key in ('file_hash', 'cache_token'):
                _require(isinstance(extraction[key], str) and re.fullmatch('[0-9a-f]{64}', extraction[key]) is not None,
                         '保存的文字提取摘要无效')
            _require(_text(extraction['ocr_engine']) if extraction['extraction_method'] == 'local_ocr'
                     else extraction['ocr_engine'] is None or isinstance(extraction['ocr_engine'], str), '保存的 OCR 引擎无效')
        if 'user_edited' in item:
            _require(type(item['user_edited']) is bool, '资料编辑标记无效')
        if item['kind'] != 'attachment':
            return
        metadata = item.get('attachment')
        _require(re.fullmatch('[0-9a-f]{32}', item['id']) is not None and isinstance(metadata, dict)
                 and {'name', 'kind', 'media_type', 'preservation_note', 'size', 'sha256'}.issubset(metadata), '附件副本元数据无效')
        _require(all(isinstance(metadata[key], str) for key in ('name', 'kind', 'media_type', 'preservation_note'))
                 and type(metadata['size']) is int and metadata['size'] >= 0
                 and isinstance(metadata['sha256'], str) and re.fullmatch('[0-9a-f]{64}', metadata['sha256']) is not None,
                 '附件副本大小或摘要无效')
        _require(item['id'] not in attachments or attachments[item['id']] == metadata, '同一附件在当前资料与历史版本中的元数据不一致')
        attachments[item['id']] = metadata

    for item in decoded['library_item'].values():
        validate_item(item)
    versions = decoded['library_version']
    for version in versions.values():
        _require(version['folder_id'] in folders and _text(version['name']), '专题版本引用了不存在的资料夹或名称无效')
        previous = version['previous_id']
        _require(previous is None or isinstance(previous, str), '专题版本前序编号无效')
        if version['number'] == 1:
            _require(previous is None, '专题第一版不能引用前序版本')
        else:
            _require(previous in versions and versions[previous]['folder_id'] == version['folder_id']
                     and versions[previous]['number'] == version['number'] - 1, '专题历史版本链不完整或包含循环')
        ids = set()
        for item in version['items']:
            _require(_text(item['id']) and item['id'] not in ids, '专题版本包含重复或无效资料编号')
            ids.add(item['id'])
            validate_item(item, version['folder_id'])
    version_numbers = [(version['folder_id'], version['number']) for version in versions.values()]
    _require(len(set(version_numbers)) == len(version_numbers), '专题包含重复版本序号')

    matters = decoded['matter']
    for matter in matters.values():
        validate_source(matter['source'])
        _require(_text(matter['title']) and (matter['due_at'] is None or type(matter['due_at']) is int and matter['due_at'] >= 0)
                 and type(matter['reminder_enabled']) is bool
                 and (not matter['reminder_enabled'] or matter['due_at'] is not None), '事项期限或提醒设置无效')
        _require(type(matter['revision']) is int and matter['revision'] >= 1
                 and type(matter['reminder_revision']) is int and matter['reminder_revision'] >= 1
                 and (matter['notified_revision'] is None or type(matter['notified_revision']) is int
                      and 1 <= matter['notified_revision'] <= matter['reminder_revision']), '事项修订状态无效')
        _require(len(matter['history']) == matter['revision'], '事项历史与修订次数不一致')
        for index, entry in enumerate(matter['history']):
            _require(isinstance(entry, dict) and isinstance(entry.get('kind'), str)
                     and entry['kind'] in {'created', 'updated', 'rescheduled'} and _number(entry.get('at')),
                     '事项历史记录结构无效')
            _require((entry['kind'] == 'created') == (index == 0), '事项创建历史位置无效')
            if entry['kind'] in {'created', 'rescheduled'}:
                validate_source(entry.get('source'))
                _require('due_at' in entry and (entry['due_at'] is None or type(entry['due_at']) is int and entry['due_at'] >= 0),
                         '事项历史期限无效')
            if entry['kind'] != 'created':
                _require(isinstance(entry.get('before'), dict) and isinstance(entry.get('after'), dict), '事项历史变更字段无效')
    event_ids, keys = set(), set()
    for event in manifest['reminders']:
        _require(isinstance(event, dict) and set(event) == {'id', 'account', 'kind', 'body', 'unique_key', 'delivered', 'created'},
                 '事项提醒数据结构无效')
        _require(type(event['id']) is int and 0 < event['id'] < 2**63 and event['id'] not in event_ids
                 and event['account'] == account and event['kind'] == 'notification'
                 and type(event['delivered']) is int and event['delivered'] == 0 and _number(event['created'])
                 and isinstance(event['body'], str) and _text(event['unique_key']) and event['unique_key'] not in keys,
                 '事项提醒编号、账号或投递状态无效')
        body = _json(event['body'])
        _require(isinstance(body, dict) and isinstance(body.get('target'), dict)
                 and isinstance(body.get('title'), str) and isinstance(body.get('body'), str), '事项提醒内容或目标无效')
        target = body['target']
        _require(target.get('account') == account and isinstance(target.get('matter_id'), str)
                 and target['matter_id'] in matters, '事项提醒引用了其他账号或不存在的事项')
        matter = matters[target['matter_id']]
        _require(matter['status'] != 'done' and matter['reminder_enabled']
                 and matter['notified_revision'] == matter['reminder_revision']
                 and event['unique_key'] == f"matter:{matter['id']}:{matter['reminder_revision']}", '事项提醒与事项修订状态不一致')
        event_ids.add(event['id'])
        keys.add(event['unique_key'])
    counts = dict(zip(('matters', 'folders', 'items', 'versions', 'searches'), (len(decoded[kind]) for kind in KINDS)))
    counts.update(notes=sum(bool(item['notes'].strip()) for item in decoded['library_item'].values()),
                  reports=sum(item['kind'] == 'report' for item in decoded['library_item'].values()),
                  attachments=len(attachments), pending_reminders=len(manifest['reminders']))
    return attachments, {'status': 'verified', 'account': account, 'counts': counts,
                        'attachment_bytes': sum(item['size'] for item in attachments.values()),
                        'content_sha256': hashlib.sha256(_encoded(manifest)).hexdigest()}


def _capture(db, account):
    placeholders = ','.join('?' for _ in KINDS)
    records = [dict(row) for row in db.execute(f'SELECT kind,id,account,body,updated FROM records '
        f'WHERE account=? AND kind IN ({placeholders}) ORDER BY kind,id', (account, *KINDS))]
    reminders = [dict(row) for row in db.execute("SELECT id,account,kind,body,unique_key,delivered,created FROM events "
        "WHERE account=? AND kind='notification' AND delivered=0 AND json_type(body,'$.target.matter_id') IS NOT NULL ORDER BY id",
        (account,))]
    return dict(format=_FORMAT, version=1, account=account, records=records, reminders=reminders)


def _copy(source, destination, expected):
    digest, size = hashlib.sha256(), 0
    while chunk := source.read(_CHUNK):
        destination.write(chunk)
        digest.update(chunk)
        size += len(chunk)
    _require(size == expected['size'] and digest.hexdigest() == expected['sha256'], '附件实体与记录中的大小或 SHA-256 不一致')


def _new_destination(destination):
    destination = Path(destination).absolute()
    _plain_path(destination)
    if destination.exists():
        raise FileExistsError(f'备份或恢复目标已存在，不会覆盖：{destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination


def export_backup(library: LibraryService, account: str, destination: Path) -> dict:
    """The request holds its account work lease; reserve referenced copies during I/O."""
    _require(_text(account), '请选择明确的备份账号')
    destination = _new_destination(destination)
    reserved, temporary = [], None
    try:
        with library.ai.account_lifecycle_lock, library.store.connection() as db:
            owner = library.resolve_account(account)
            manifest = _capture(db, owner)
            attachments, report = _validate_manifest(manifest)
            for id in attachments:
                path = library.attachment_path(owner, id)
                _plain_path(path)
                _require(path.is_file(), f'资料附件副本缺失：{id}')
                key = str(path.resolve())
                _attachment_downloads[key] = _attachment_downloads.get(key, 0) + 1
                reserved.append((id, path, key))
        descriptor, name = tempfile.mkstemp(prefix='.application-backup-', suffix='.zip', dir=destination.parent)
        os.close(descriptor)
        temporary = Path(name)
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            archive.writestr('manifest.json', _encoded(manifest))
            for id, path, _key in reserved:
                _plain_path(path)
                with path.open('rb') as source, archive.open(f'attachments/{id}/content', 'w', force_zip64=True) as target:
                    _copy(source, target, attachments[id])
            write_zip_checksums(archive, uuid.uuid4().hex)
        with zipfile.ZipFile(temporary) as archive:
            validate_zip_checksums(archive)
        # A same-directory hard link publishes atomically and cannot overwrite an existing file.
        os.link(temporary, destination)
        return report
    except ArchiveChecksumError as error:
        raise ApplicationBackupError(str(error)) from error
    finally:
        with library.store.lock:
            for _id, _path, key in reserved:
                _attachment_downloads[key] -= 1
                if not _attachment_downloads[key]:
                    del _attachment_downloads[key]
        if temporary is not None:
            temporary.unlink()


def restore_backup(source: BinaryIO | Path, destination: Path) -> dict:
    """Restore into a new isolated directory; never merge into the running AIStore."""
    destination = _new_destination(destination)
    staging = None
    try:
        with zipfile.ZipFile(source) as archive:
            files = validate_zip_checksums(archive)
            _require('manifest.json' in files, '应用资料备份缺少清单')
            manifest = _json(archive.read('manifest.json'))
            attachments, report = _validate_manifest(manifest)
            expected_files = {'manifest.json', *(f'attachments/{id}/content' for id in attachments)}
            _require(set(files) == expected_files, '应用资料备份包含未声明文件或缺失附件')
            for id, metadata in attachments.items():
                _require(files[f'attachments/{id}/content'] == {key: metadata[key] for key in ('size', 'sha256')},
                         '备份附件与资料记录中的摘要不一致')
            staging = Path(tempfile.mkdtemp(prefix='.application-restore-', dir=destination.parent))
            ai_root = staging / 'ai'
            ai_root.mkdir()
            account_hash = hashlib.sha256(manifest['account'].encode()).hexdigest()
            for id, metadata in attachments.items():
                path = ai_root / 'library' / account_hash / id / 'content'
                path.parent.mkdir(parents=True)
                with archive.open(f'attachments/{id}/content') as reader, path.open('xb') as writer:
                    _copy(reader, writer, metadata)
        db = sqlite3.connect(ai_root / 'ai.sqlite3')
        db.row_factory = sqlite3.Row
        try:
            with db:
                db.executescript(SCHEMA_SQL)
                db.executemany('INSERT INTO records(kind,id,account,body,updated) VALUES(:kind,:id,:account,:body,:updated)', manifest['records'])
                db.executemany('INSERT INTO events(id,account,kind,body,unique_key,delivered,created) '
                               'VALUES(:id,:account,:kind,:body,:unique_key,:delivered,:created)', manifest['reminders'])
            _require(db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', '恢复数据库完整性检查失败')
            restored = _capture(db, manifest['account'])
            _require(_encoded(restored) == _encoded(manifest), '恢复后的内容与备份记录不一致')
        finally:
            db.close()
        # Re-read the finished files, rather than counting successful writes as verification.
        for id, metadata in attachments.items():
            path = ai_root / 'library' / account_hash / id / 'content'
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                while chunk := stream.read(_CHUNK):
                    digest.update(chunk)
            _require(path.stat().st_size == metadata['size'] and digest.hexdigest() == metadata['sha256'],
                     '恢复后的附件完整性检查失败')
        report = {**report, 'restore_path': str(destination.resolve())}
        (staging / 'verification.json').write_bytes(_encoded(report))
        _plain_path(destination)
        if destination.exists():
            raise FileExistsError(f'恢复目标已存在，不会覆盖：{destination}')
        os.rename(staging, destination)
        staging = None
        return report
    except (ArchiveChecksumError, zipfile.BadZipFile, EOFError) as error:
        raise ApplicationBackupError(f'应用资料备份校验失败：{error}') from error
    finally:
        if staging is not None:
            shutil.rmtree(staging)


def _merge_plan(db, incoming, current):
    account = incoming['account']
    local = {(row['kind'], row['id']): row for row in current['records']}
    bodies = {}
    for key, row in local.items():
        try:
            bodies[key] = (MatterService.decode(row) if row['kind'] == 'matter'
                           else LibraryService.decode(row, row['kind'], account))
        except (ValueError, TypeError, KeyError) as error:
            raise ApplicationBackupConflict(f'本机资料记录损坏，未覆盖或写入资料：{row["kind"]}/{row["id"]}') from error
    for row in incoming['records']:
        existing = db.execute('SELECT account FROM records WHERE kind=? AND id=?', (row['kind'], row['id'])).fetchone()
        if existing is not None and existing['account'] != account:
            raise ApplicationBackupConflict(f'资料编号已由其他账号占用，未写入任何资料：{row["kind"]}/{row["id"]}')
    added = dict.fromkeys((*_COUNT_KEYS.values(), 'attachments', 'pending_reminders'), 0)
    kept, skipped, additions = dict(added), [], []
    versions = {id: body for (kind, id), body in bodies.items() if kind == 'library_version'}
    positions = {(body['folder_id'], body['number']): id for id, body in versions.items()}
    blocked_versions = set()
    rows = sorted(incoming['records'], key=lambda row: (
        row['kind'], _json(row['body'])['number'] if row['kind'] == 'library_version' else 0, row['id']))
    for row in rows:
        kind, id = row['kind'], row['id']
        body, key = _json(row['body']), (kind, id)
        if key in local:
            kept[_COUNT_KEYS[kind]] += 1
            if bodies[key] != body:
                skipped.append(dict(kind=kind, id=id, reason='local_record_kept'))
                if kind == 'library_version':
                    blocked_versions.add(id)
            continue
        if kind == 'library_version':
            position = (body['folder_id'], body['number'])
            previous = body['previous_id']
            reason = None
            if position in positions:
                reason = 'version_number_conflict'
            elif previous is not None and previous in blocked_versions:
                reason = 'conflicting_previous_version'
            elif previous is not None and previous not in versions:
                reason = 'missing_previous_version'
            if reason is not None:
                skipped.append(dict(kind=kind, id=id, reason=reason))
                blocked_versions.add(id)
                continue
            positions[position], versions[id] = id, body
        additions.append(row)
        added[_COUNT_KEYS[kind]] += 1
    new_matters = {row['id'] for row in additions if row['kind'] == 'matter'}
    reminders = []
    for event in incoming['reminders']:
        target = _json(event['body'])['target']
        id = target['matter_id']
        if id not in new_matters:
            kept['pending_reminders'] += 1
            skipped.append(dict(kind='notification', id=event['unique_key'], reason='local_matter_kept'))
            continue
        existing = db.execute('SELECT account,kind,body FROM events WHERE unique_key=?', (event['unique_key'],)).fetchone()
        if existing is not None:
            existing_body = _json(existing['body'])
            if (existing['account'] != account or existing['kind'] != 'notification'
                    or not isinstance(existing_body, dict) or existing_body.get('target') != target):
                raise ApplicationBackupConflict('提醒唯一键已对应其他账号或其他目标，未写入任何资料')
            kept['pending_reminders'] += 1
            skipped.append(dict(kind='notification', id=event['unique_key'], reason='existing_notification_kept'))
            continue
        reminders.append(event)
    added['pending_reminders'] = len(reminders)
    next_id = max((event['id'] for event in current['reminders']), default=0)
    proposed = {**current, 'records': sorted([*current['records'], *additions], key=lambda row: (row['kind'], row['id'])),
                'reminders': [*current['reminders'], *({**event, 'id': next_id + index} for index, event in enumerate(reminders, 1))]}
    try:
        attachments, _ = _validate_manifest(proposed)
    except ApplicationBackupError as error:
        raise ApplicationBackupConflict(f'保留本机资料后无法形成完整的资料关系，未写入任何资料：{error}') from error
    return dict(records=additions, reminders=reminders, attachments=attachments, added=added, kept=kept, skipped=skipped)


def _file_state(path):
    value = path.stat()
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _verify_existing_file(path, metadata):
    _plain_path(path)
    before = _file_state(path)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(_CHUNK):
            digest.update(chunk)
    if (before != _file_state(path) or before[2] != metadata['size'] or digest.hexdigest() != metadata['sha256']):
        raise ApplicationBackupConflict(f'现有附件内容与资料摘要不一致，未覆盖该文件：{path.name}')
    return before


def apply_backup(library: LibraryService, verified_directory: Path) -> dict:
    """Fill missing live data; the caller validates the account and holds its work lease."""
    directory = Path(verified_directory).absolute()
    _plain_path(directory)
    verification = _json((directory / 'verification.json').read_bytes())
    _require(isinstance(verification, dict) and verification.get('status') == 'verified'
             and _text(verification.get('account')), '恢复目录缺少有效校验结果')
    account = verification['account']
    database = directory / 'ai' / 'ai.sqlite3'
    _plain_path(database)
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        incoming = _capture(db, account)
    source_attachments, report = _validate_manifest(incoming)
    _require(all(verification.get(key) == report[key] for key in ('counts', 'attachment_bytes', 'content_sha256')),
             '隔离资料在校验后发生变化，请重新上传备份')
    reservations, files = [], []
    try:
        with library.ai.account_lifecycle_lock, library.store.connection() as db:
            current = _capture(db, account)
            plan = _merge_plan(db, incoming, current)
            for id, metadata in source_attachments.items():
                if plan['attachments'].get(id) != metadata:
                    plan['skipped'].append(dict(kind='attachment', id=id, reason='local_metadata_or_dependency_kept'))
                    continue
                path = library.attachment_path(account, id)
                _plain_path(path)
                exists = path.exists()
                if exists:
                    if not path.is_file():
                        raise ApplicationBackupConflict('附件位置已存在其他类型的本机数据，未覆盖')
                    key = str(path.resolve())
                    _attachment_downloads[key] = _attachment_downloads.get(key, 0) + 1
                    reservations.append(key)
                files.append(dict(id=id, path=path, metadata=metadata, exists=exists))
        # Same-volume temporary files make the locked commit a sequence of fast links.
        temporary_owner = tempfile.TemporaryDirectory(prefix='.application-apply-', dir=library.store.root)
        committed, cleanup_error = False, None
        try:
            temporary = temporary_owner.name
            source_root = directory / 'ai' / 'library' / hashlib.sha256(account.encode()).hexdigest()
            for entry in files:
                if entry['exists']:
                    entry['state'] = _verify_existing_file(entry['path'], entry['metadata'])
                else:
                    source = source_root / entry['id'] / 'content'
                    _plain_path(source)
                    entry['staged'] = Path(temporary) / entry['id']
                    with source.open('rb') as reader, entry['staged'].open('xb') as writer:
                        _copy(reader, writer, entry['metadata'])
            installed, created_parents = [], []
            with library.ai.account_lifecycle_lock, library.store.lock:
                try:
                    with library.store.connection() as db:
                        db.execute('BEGIN IMMEDIATE')
                        if _encoded(_capture(db, account)) != _encoded(current):
                            raise ApplicationBackupConflict('文件准备期间本机资料已变化，请重新确认后恢复；未写入资料')
                        # Recheck global identifiers and notification keys while the write transaction owns them.
                        final = _merge_plan(db, incoming, current)
                        final['skipped'].extend(entry for entry in plan['skipped'] if entry['kind'] == 'attachment')
                        for entry in files:
                            path = entry['path']
                            _plain_path(path)
                            if entry['exists']:
                                if not path.is_file() or _file_state(path) != entry['state']:
                                    raise ApplicationBackupConflict('本机附件在准备期间发生变化；未写入资料')
                                final['kept']['attachments'] += 1
                            else:
                                if path.exists():
                                    raise ApplicationBackupConflict('待补附件位置在准备期间已出现本机文件；未覆盖')
                                if not path.parent.exists():
                                    path.parent.mkdir(parents=True)
                                    created_parents.append(path.parent)
                                os.link(entry['staged'], path)
                                installed.append(path)
                                final['added']['attachments'] += 1
                        db.executemany('INSERT INTO records(kind,id,account,body,updated) VALUES(:kind,:id,:account,:body,:updated)', final['records'])
                        db.executemany('INSERT INTO events(account,kind,body,unique_key,delivered,created) '
                            'VALUES(:account,:kind,:body,:unique_key,:delivered,:created)', final['reminders'])
                        _validate_manifest(_capture(db, account))
                except BaseException:
                    # Database rollback has finished; readers remain excluded until new files are removed.
                    for path in reversed(installed):
                        path.unlink()
                    for parent in reversed(created_parents):
                        parent.rmdir()
                    raise
                committed = True
                library.ai.deleted_accounts.discard(account)
                library.store.revoked_accounts.discard(account)
        finally:
            try:
                temporary_owner.cleanup()
            except OSError as error:
                if not committed:
                    raise
                cleanup_error = str(error)
        return {**report, 'status': 'restored', 'added': final['added'], 'kept': final['kept'],
                'skipped': final['skipped'], 'data_path': str(library.store.root.resolve()),
                **({'cleanup_error': cleanup_error} if cleanup_error is not None else {})}
    finally:
        with library.store.lock:
            for key in reservations:
                _attachment_downloads[key] -= 1
                if not _attachment_downloads[key]:
                    del _attachment_downloads[key]
