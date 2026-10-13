"""Local-only cross-year history browsing; no model calls or copied history."""
from datetime import date
import logging
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from .ai import account_name, local_only
from ..account_workers import account_to_thread
from ..chat_helpers import _resolve_account_dir
from .. import time_machine_service as service


router = APIRouter(prefix='/api/time-machine', dependencies=[Depends(local_only)])
logger = logging.getLogger(__name__)


async def _read(account, function, **kwargs):
    if not account.strip():
        raise HTTPException(422, '请选择明确的微信账号')
    owner = account_name(account)
    try:
        return await account_to_thread(_resolve_account_dir(owner), function, owner, **kwargs)
    except (sqlite3.Error, ValueError, KeyError, OSError) as error:
        logger.error('time_machine.%s failed account=%s error_type=%s', function.__name__, owner, type(error).__name__, exc_info=True)
        raise HTTPException(503, '无法完整读取本地消息索引，请重试；若持续失败，请重新建立索引。') from error


@router.get('/catalog')
async def catalog(account: str = Query(min_length=1), username: str = ''):
    return await _read(account, service.catalog, username=username)


@router.get('/messages')
async def messages(account: str, start: date, end: date, revision: str = Query(min_length=1), username: str = '',
                   offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100)):
    service.date_window(start, end)
    return await _read(account, service.messages, start=start, end=end, revision=revision,
                       username=username, offset=offset, limit=limit)


@router.post('/refresh-index')
async def refresh_index(account: str = Query(min_length=1)):
    return await _read(account, service.refresh_index)
