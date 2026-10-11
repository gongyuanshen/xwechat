from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .ai import local_only, account_name
from ..account_workers import AccountFileResponse, account_to_thread
from ..ai.service import get_ai_service
from ..attachment_service import authoritative_message, browse_attachments, extract_document, read_extracted_text, resolve_attachment
from ..chat_helpers import _resolve_account_dir
from ..snapshot_registry import account_work


router = APIRouter(prefix='/api/attachments', dependencies=[Depends(local_only)])


class AttachmentSource(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    username: str = Field(min_length=1)
    anchor: str = Field(min_length=1)


class AttachmentExtraction(AttachmentSource):
    ocr: bool = False


@router.get('')
async def attachments(account: str, kind: Literal['all', 'file', 'image', 'video'] = 'all', q: str = '',
                      search_in: Literal['name', 'body', 'all'] = 'name', username: str = '', sender: str = '',
                      start_time: int | None = Query(None, ge=0), end_time: int | None = Query(None, ge=0),
                      offset: int = Query(0, ge=0), limit: int = Query(40, ge=1, le=200)):
    if start_time is not None and end_time is not None and start_time > end_time:
        raise HTTPException(422, '开始时间不能晚于结束时间')
    owner = account_name(account)
    return await account_to_thread(_resolve_account_dir(owner), browse_attachments, get_ai_service().store, owner,
        kind=kind, q=q.strip(), search_in=search_in, username=username, sender=sender,
        start_time=start_time, end_time=end_time, offset=offset, limit=limit)


@router.get('/filters')
async def filters(account: str):
    owner = account_name(account)
    return await account_to_thread(_resolve_account_dir(owner), browse_attachments, get_ai_service().store, owner, filters=True)


@router.get('/content')
async def content(request: Request, account: str, username: str = Query(min_length=1), anchor: str = Query(min_length=1), download: bool = True):
    owner = account_name(account)
    account_dir = _resolve_account_dir(owner)
    with account_work(account_dir):
        message = await authoritative_message(request, owner, AttachmentSource(username=username, anchor=anchor))
        resolved = await resolve_attachment(request, owner, username, message)
        disposition = 'attachment' if download or resolved['kind'] == 'file' else 'inline'
        headers = {'Content-Disposition': f"{disposition}; filename*=UTF-8''{quote(resolved['name'])}", 'X-Content-Type-Options': 'nosniff'}
        if resolved['path'] is not None:
            return AccountFileResponse(resolved['path'], account_dir=account_dir, media_type=resolved['media_type'], headers=headers)
        return Response(resolved['data'], media_type=resolved['media_type'], headers=headers)


@router.post('/extract')
async def extract(body: AttachmentExtraction, request: Request, account: str):
    owner = account_name(account)
    account_dir = _resolve_account_dir(owner)
    with account_work(account_dir):
        message = await authoritative_message(request, owner, body)
        resolved = await resolve_attachment(request, owner, body.username, message)
        return await account_to_thread(account_dir, extract_document, get_ai_service(), owner, body.username, message, resolved, ocr=body.ocr)


@router.get('/text')
async def extracted_text(request: Request, account: str, username: str = Query(min_length=1), anchor: str = Query(min_length=1)):
    owner = account_name(account)
    account_dir = _resolve_account_dir(owner)
    with account_work(account_dir):
        message = await authoritative_message(request, owner, AttachmentSource(username=username, anchor=anchor))
        def read():
            with get_ai_service().store.connection() as db:
                return read_extracted_text(db, owner, username, message)
        return await account_to_thread(account_dir, read)
