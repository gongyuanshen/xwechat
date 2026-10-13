import html
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

from .ai import local_only
from ..application_accounts import application_account_name as account_name, application_account_work, list_application_accounts
from .chat import (get_chat_messages_around, _parse_message_anchor_local_id, _iter_message_db_paths,
                   _resolve_msg_table_name, _resolve_account_dir)
from ..ai.service import get_ai_service
from ..ai.agent_service import get_agent_service
from ..library_service import LibraryService, LibraryFileResponse
from ..account_workers import account_to_thread
from ..snapshot_registry import account_work

router = APIRouter(prefix='/api/library', dependencies=[Depends(local_only)])


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Name(Input):
    name: str = Field(min_length=1, max_length=200)

    @field_validator('name')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('名称不能为空')
        return value.strip()


class Criteria(Input):
    scope: Literal['conversation', 'global']
    username: str
    query: str
    retrieval_mode: Literal['keyword', 'hybrid']
    render_types: Literal['', 'text', 'image', 'emoji', 'video', 'voice', 'file', 'link', 'quote',
                          'chatHistory', 'transfer', 'redPacket', 'location', 'voip', 'system'] = 'text'
    sender: str = ''
    session_type: Literal['', 'group', 'single'] = ''
    start_time: int | None = Field(default=None, ge=0)
    end_time: int | None = Field(default=None, ge=0)
    relative_days: int | None = Field(default=None, ge=1, le=3650)

    @model_validator(mode='after')
    def valid_range(self):
        if self.scope == 'conversation' and not self.username.strip():
            raise ValueError('会话搜索必须指定会话')
        if self.start_time is not None and self.end_time is not None and self.start_time > self.end_time:
            raise ValueError('开始时间不能晚于结束时间')
        if self.relative_days is not None and (self.start_time is not None or self.end_time is not None):
            raise ValueError('滚动日期不能同时保存固定起止时间')
        if not self.query.strip() and (self.retrieval_mode != 'keyword' or not self.render_types):
            raise ValueError('请输入关键词，或在关键词模式下选择具体消息类型')
        return self


class Search(Name):
    criteria: Criteria


class DossierVersion(Name):
    expected_token: str = Field(pattern='^[0-9a-f]{64}$')
    arrangements: str = Field(default='', max_length=20000)
    conclusions: str = Field(default='', max_length=20000)


class SearchEdit(Input):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    criteria: Criteria | None = None

    @model_validator(mode='after')
    def valid_fields(self):
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError('不能保存空值')
        if self.name is not None and not self.name.strip():
            raise ValueError('名称不能为空')
        return self


class Source(Input):
    username: str = Field(min_length=1)
    anchor: str = Field(min_length=1)
    text: str | None = None
    sender: str | None = None
    create_time: int | None = None
    render_type: str | None = None


class Item(Input):
    folder_id: str = Field(min_length=1)
    kind: Literal['message', 'report', 'attachment', 'extracted_text']
    title: str = Field(default='', max_length=200)
    notes: str = ''
    tags: list[str] = Field(default_factory=list, max_length=100)
    source: Source | None = None
    run_id: str | None = None
    cache_token: str | None = Field(default=None, pattern='^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def provenance(self):
        if self.kind in {'message', 'attachment', 'extracted_text'} and (self.source is None or self.run_id is not None):
            raise ValueError('消息资料必须提供来源')
        if self.kind == 'report' and (not self.run_id or self.source is not None):
            raise ValueError('报告资料必须提供任务编号')
        if self.kind == 'extracted_text' and self.cache_token is None:
            raise ValueError('提取文字资料必须提供当前缓存版本')
        if self.kind != 'extracted_text' and self.cache_token is not None:
            raise ValueError('只有提取文字资料能提供缓存版本')
        return self


class ItemEdit(Input):
    folder_id: str | None = Field(default=None, min_length=1)
    title: str | None = Field(default=None, max_length=200)
    content: str | None = None
    notes: str | None = None
    tags: list[str] | None = Field(default=None, max_length=100)
    verified: bool | None = None

    @model_validator(mode='after')
    def nonnull(self):
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError('不能保存空值')
        return self


def service():
    return LibraryService(get_ai_service(), account_name)


@router.get('/accounts')
def accounts():
    return list_application_accounts()


def validate_message_source(account, source):
    stem, table, _ = _parse_message_anchor_local_id(source.anchor)
    account_dir = _resolve_account_dir(account)
    paths = [path for path in _iter_message_db_paths(account_dir) if path.stem == stem]
    if len(paths) != 1:
        raise HTTPException(404, '消息来源数据库不存在')
    with closing(sqlite3.connect(paths[0].resolve().as_uri() + '?mode=ro', uri=True)) as db:
        actual_table = _resolve_msg_table_name(db, source.username)
    if not table or not actual_table or table.casefold() != actual_table.casefold():
        raise HTTPException(422, '消息来源不属于指定会话')


@router.get('/folders')
def folders(account: str):
    return service().list('library_folder', account_name(account))


@router.post('/folders')
def create_folder(body: Name, account: str):
    return service().create('library_folder', account, body.model_dump())


@router.patch('/folders/{id}')
def edit_folder(id: str, body: Name, account: str):
    return service().update('library_folder', id, account, body.model_dump())


@router.delete('/folders/{id}')
def delete_folder(id: str, account: str):
    return service().delete('library_folder', id, account)


@router.get('/folders/{id}/dossier')
def dossier(id: str, account: str):
    return service().dossier(account_name(account), id)


@router.post('/folders/{id}/versions')
def save_version(id: str, body: DossierVersion, account: str):
    return service().save_version(account, id, body.model_dump())


@router.get('/folders/{id}/versions/{version_id}')
def get_version(id: str, version_id: str, account: str):
    return service().version(account_name(account), id, version_id)


@router.get('/folders/{id}/versions/{version_id}/attachments/{item_id}')
def version_attachment(id: str, version_id: str, item_id: str, account: str, download: bool = True):
    owner = account_name(account)
    with application_account_work(owner) as account_dir:
        svc = service()
        with svc.store.connection() as db:
            item = svc.version_attachment(db, owner, id, version_id, item_id)
            return attachment_response(svc, item, account_dir, download)


@router.get('/searches')
def searches(account: str):
    return service().list('library_search', account_name(account))


@router.post('/searches')
def create_search(body: Search, account: str):
    return service().create('library_search', account, body.model_dump())


@router.patch('/searches/{id}')
def edit_search(id: str, body: SearchEdit, account: str):
    return service().update('library_search', id, account, body.model_dump(exclude_unset=True))


@router.delete('/searches/{id}')
def delete_search(id: str, account: str):
    return service().delete('library_search', id, account)


@router.get('/items')
def items(account: str, folder_id: str | None = None):
    return service().list('library_item', account_name(account), folder_id)


@router.get('/items/{id}')
def get_item(id: str, account: str):
    svc = service()
    with svc.store.connection() as db:
        return svc.get(db, 'library_item', id, account_name(account))


@router.post('/items')
async def create_item(body: Item, account: str, request: Request):
    owner = account_name(account)
    svc = service()
    if body.kind in {'attachment', 'extracted_text'}:
        from ..attachment_service import authoritative_message, resolve_attachment
        account_dir = _resolve_account_dir(owner)
        with account_work(account_dir):
            message = await authoritative_message(request, owner, body.source)
            source = {'username': body.source.username, 'anchor': message['id'], 'text': message['content'],
                      'sender': message['senderUsername'], 'create_time': message['createTime'], 'render_type': message['renderType']}
            if any(value is not None and value != source[key] for key, value in body.source.model_dump().items() if key not in ('anchor', 'username')):
                raise HTTPException(422, '附件内容或身份与原始记录不一致')
            if body.kind == 'extracted_text':
                return await account_to_thread(account_dir, svc.create_extracted_text, owner, {
                    'folder_id': body.folder_id, 'kind': 'extracted_text', 'title': body.title,
                    'notes': body.notes, 'tags': body.tags, 'verified': False, 'user_edited': False,
                    'source': source, 'report': None}, message, body.cache_token)
            resolved = await resolve_attachment(request, owner, body.source.username, message)
            return await account_to_thread(account_dir, svc.create_attachment, owner, {
                'folder_id': body.folder_id, 'kind': 'attachment', 'title': body.title or resolved['name'],
                'content': message['content'], 'notes': body.notes, 'tags': body.tags, 'verified': False,
                'user_edited': False, 'source': source, 'report': None}, resolved)
    source, report = None, None
    if body.kind == 'message':
        validate_message_source(owner, body.source)
        evidence = await get_chat_messages_around(request=request, username=body.source.username,
            anchor_id=body.source.anchor, account=owner, before=0, after=0, source=None)
        messages = evidence['messages']
        if (evidence['account'] != owner or evidence['username'] != body.source.username or len(messages) != 1
                or evidence['anchorIndex'] != 0 or messages[0]['id'] != evidence['anchorId']
                or evidence['anchorId'].casefold() != body.source.anchor.casefold()):
            raise HTTPException(422, '消息来源与请求不一致')
        message = messages[0]
        source = {'username': body.source.username, 'anchor': evidence['anchorId'], 'text': message['content'],
                  'sender': message['senderUsername'], 'create_time': message['createTime'], 'render_type': message['renderType']}
        if any(value is not None and value != source[key] for key, value in body.source.model_dump().items() if key not in ('anchor', 'username')):
            raise HTTPException(422, '消息内容或身份与原始记录不一致')
        content = source['text']
    else:
        run = svc.store.get('agent_run', body.run_id)
        if run is None or run['account'] != owner:
            raise HTTPException(404, '分析任务不存在')
        if run['status'] != 'completed' or not run.get('answer'):
            raise HTTPException(409, '只能保存已完成且有回答的分析任务')
        agent = get_agent_service()
        report = agent.public_run(body.run_id, owner)
        cited_ids = [citation['source'] for citation in report.get('citations', [])]
        # Public citations are UI excerpts. Copy complete authority records while
        # the source workspace still exists, so deleting its thread loses no evidence.
        originals = agent.workspace.evidence(body.run_id).get_many(cited_ids) if cited_ids else {}
        if set(originals) != set(cited_ids):
            raise HTTPException(409, '分析报告的原始引用证据缺失')
        content = run['answer']
        metadata_fields = ('id', 'version', 'created', 'started_at', 'finished_at', 'query_scope', 'query_filters', 'time_range',
                           'timezone_offset', 'read_count', 'coverage_state', 'coverage_warnings', 'source_count', 'references', 'citations')
        report = {key: report[key] for key in metadata_fields if key in report}
        report['answer'] = content
        evidence_keys = ('source', 'username', 'name', 'sender', 'sender_id', 'time', 'text', 'anchor', 'kind', 'media')
        report['evidence'] = [{key: original[key] for key in evidence_keys if key in original} for original in originals.values()]
        report['model_metadata'] = {role: {key: run[role][key] for key in ('name', 'provider', 'model', 'model_id') if key in run[role]}
                                    for role in ('profile', 'vision') if role in run}
    return await run_in_threadpool(svc.create, 'library_item', owner, {'folder_id': body.folder_id, 'kind': body.kind,
        'title': body.title or re.sub(r'\[\[[^\]]+\]\]', '', content).strip()[:80] or '消息资料', 'content': content, 'notes': body.notes,
        'tags': body.tags, 'verified': False, 'user_edited': False, 'source': source, 'report': report})


@router.patch('/items/{id}')
def edit_item(id: str, body: ItemEdit, account: str):
    return service().update('library_item', id, account, body.model_dump(exclude_unset=True))


@router.delete('/items/{id}')
def delete_item(id: str, account: str):
    return service().delete('library_item', id, account)


@router.get('/items/{id}/export')
def export_item(id: str, account: str, format: Literal['markdown', 'html'] = 'markdown'):
    item = get_item(id, account)
    report = item['report']
    def readable(text):
        if report is None:
            return text
        citations = {c['source']: c for c in report.get('citations', [])}
        numbers = {source: index for index, source in enumerate(citations, 1)}
        refs = report.get('references', [])
        refs = list(refs.values()) if isinstance(refs, dict) else refs
        references = {r['id']: r for r in refs if 'id' in r}
        def replace(match):
            kind, id = match.groups()
            ref = references.get(id.lower()) if kind in ('person', 'image') else citations.get(id.lower())
            if ref is None:
                return f'〔来源待核实：{id}〕'
            if kind in ('person', 'image'):
                return ref.get('name') or ref.get('label') or id
            return f"〔{numbers[id.lower()]} · {ref.get('name') or ref.get('username', '')} · {ref.get('sender', '')}〕"
        return re.sub(r'\[\[(?:(person|image|source):)?([a-f0-9]{24})\]\]', replace, text, flags=re.I)
    sections = [('内容', readable(item['content'])), ('核验', '已人工核验' if item['verified'] else '未核验'),
                ('笔记', item['notes']), ('标签', ', '.join(item['tags']))]
    if item['kind'] == 'attachment':
        attachment = item['attachment']
        sections += [('附件副本', f"文件名：{attachment['name']}\n大小：{attachment['size']} 字节\nSHA-256：{attachment['sha256']}\n{attachment['preservation_note']}\n附件实体请在资料夹中单独下载；本文档未嵌入文件。")]
    if item['kind'] == 'extracted_text':
        extraction = item['extraction']
        is_ocr = extraction['extraction_method'] == 'local_ocr'
        metadata = [f"提取方式：{'本地 OCR' if is_ocr else '本地文档正文提取'}",
                    f"缓存时间：{datetime.fromtimestamp(extraction['updated'], timezone.utc).isoformat()}",
                    f"输入文件 SHA-256：{extraction['file_hash']}", f"缓存版本：{extraction['version']}",
                    f"结果标识：{extraction['cache_token']}", '保存的是完整提取文字副本，未包含附件实体。']
        if is_ocr:
            metadata += [f"OCR 引擎：{extraction['ocr_engine']}", f"识别页 / 图片数：{extraction['ocr_units']}",
                         'OCR 文字可能有误，请核对原图；图片来源可能是缩略图。']
        sections += [('本地提取来源', '\n'.join(metadata))]
    if item.get('user_edited'):
        sections += [('编辑说明', '当前内容已由用户编辑；原始分析回答保留在下方。')]
    if item['source'] is not None:
        source = item['source']
        sections += [('原始消息来源', f"账号：{item['account']}\n会话：{source['username']}\n发送者：{source['sender']}\n消息时间：{source['create_time']}\n定位：{source['anchor']}")]
    if report is not None:
        metadata = [f"原始任务：{report['id']}"]
        for key, label in (('created', '创建时间'), ('finished_at', '完成时间')):
            if report.get(key) is not None:
                metadata.append(f"{label}：{datetime.fromtimestamp(report[key], timezone.utc).isoformat()}")
        for key, label in (('query_scope', '会话范围'), ('time_range', '时间范围'), ('query_filters', '检索条件'),
                           ('coverage_state', '覆盖状态'), ('source_count', '来源数量')):
            if key in report:
                value = report[key]
                metadata.append(f"{label}：{json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value}")
        for role, model in report['model_metadata'].items():
            metadata.append(f"模型（{role}）：" + ' · '.join(str(value) for value in model.values()))
        sections += [('分析范围与模型', '\n'.join(metadata)), ('覆盖提醒', '\n'.join(report.get('coverage_warnings', [])))]
        sources = []
        numbers = {citation['source']: index for index, citation in enumerate(report.get('citations', []), 1)}
        for citation in report.get('evidence', report.get('citations', [])):
            sources.append(f"[{numbers[citation['source']]}] {citation.get('name') or citation.get('username', '')} · {citation.get('sender', '')} · {citation.get('time', '')}\n{citation.get('text', '')}\n账号：{item['account']}\n会话：{citation.get('username', '')}\n定位：{citation.get('anchor', '')}\n来源编号：{citation['source']}")
        refs = report.get('references', [])
        for ref in (refs.values() if isinstance(refs, dict) else refs):
            sources.append(f"{ref.get('name') or ref.get('label', '')} · {ref.get('kind', '')}\n来源：{ref.get('source') or ', '.join(ref.get('sources', []))}")
        sections += [('引用来源', '\n\n'.join(sources))]
        if item.get('user_edited'):
            sections += [('原始分析回答', readable(report['answer']))]
    text = f"# {item['title']}\n\n" + '\n\n'.join(f'## {title}\n\n{body}' for title, body in sections)
    if format == 'html':
        text = ('<!doctype html><html lang="zh"><meta charset="utf-8">'
                '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
                f'<title>{html.escape(item["title"])}</title><body><h1>{html.escape(item["title"])}</h1>'
                + ''.join(f'<section><h2>{html.escape(title)}</h2><p style="white-space:pre-wrap">{html.escape(body)}</p></section>' for title, body in sections)
                + '</body></html>')
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', item['title']).strip(' .')[:100] or '资料'
    suffix = 'html' if format == 'html' else 'md'
    return Response(text, media_type='text/html' if format == 'html' else 'text/markdown',
        headers={'Content-Disposition': f"attachment; filename=library.{suffix}; filename*=UTF-8''{quote(filename + '.' + suffix)}"})


@router.get('/items/{id}/attachment')
def download_attachment(id: str, account: str, download: bool = True):
    owner = account_name(account)
    with application_account_work(owner) as account_dir:
        svc = service()
        with svc.store.connection() as db:
            item = svc.get(db, 'library_item', id, owner)
            if item['kind'] != 'attachment':
                raise HTTPException(422, '此资料不包含附件副本')
            return attachment_response(svc, item, account_dir, download)


def attachment_response(svc, item, account_dir, download):
    path = svc.attachment_path(item['account'], item['id'])
    if not path.is_file():
        raise HTTPException(410, '资料附件副本缺失，请检查本地数据目录。')
    attachment = item['attachment']
    disposition = 'attachment' if download or attachment['kind'] == 'file' else 'inline'
    return LibraryFileResponse(path, store=svc.store, account_dir=account_dir, media_type=attachment['media_type'],
        headers={'Content-Disposition': f"{disposition}; filename*=UTF-8''{quote(attachment['name'])}", 'X-Content-Type-Options': 'nosniff'})
