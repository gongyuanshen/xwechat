"""One local search contract, with explicit coverage for each source."""
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from .ai import local_only
from ..ai.service import get_ai_service
from ..application_accounts import application_account_name, application_account_work
from ..chat_accounts import list_chat_account_names
from ..chat_helpers import _resolve_account_dir
from ..snapshot_registry import account_work
from ..unified_search import search_source


router = APIRouter(prefix='/api/search', dependencies=[Depends(local_only)])


@router.get('')
def search(account: str = Query(min_length=1, pattern=r'\S'), q: str = Query(min_length=1),
           source: Literal['chat', 'documents', 'ocr', 'notes'] = 'chat',
           offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
           start_time: int | None = Query(None, ge=0), end_time: int | None = Query(None, ge=0)):
    q = q.strip()
    if not q or len(q.split()) > 8:
        raise HTTPException(422, '请输入关键词；空格分隔的关键词最多 8 个，所有关键词须同时匹配。')
    if start_time is not None and end_time is not None and start_time > end_time:
        raise HTTPException(422, '开始时间不能晚于结束时间')
    owner = application_account_name(account)
    options = dict(source=source, q=q, offset=offset, limit=limit, start_time=start_time, end_time=end_time)
    if source != 'notes' and owner not in list_chat_account_names():
        return {'account': owner, 'query': q, 'source': source, 'status': 'unavailable', 'items': [],
                'has_more': False, 'next_offset': offset, 'elapsed_ms': 0,
                'coverage': {'available': None, 'searched': None,
                             'message': '尚未导入该账号的聊天数据库；可以查询已保存的资料正文与笔记。'}}
    try:
        # The synchronous route keeps the worker and its account/snapshot lease
        # together until the actual SQLite read finishes.
        context = application_account_work(owner) if source == 'notes' else account_work(_resolve_account_dir(owner))
        with context:
            return search_source(get_ai_service().store, owner, **options)
    except (ValueError, sqlite3.Error, OSError) as error:
        raise HTTPException(500, f'统一搜索数据读取失败（{source}）：{error}') from error
