import asyncio
import json
from typing import Literal
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import StreamingResponse

from .ai import local_only, account_name as chat_account_name
from ..application_accounts import application_account_name as account_name
from ..ai.agent_schemas import ThreadInput, ThreadUpdate, TurnInput, RestartInput
from ..ai.agent_service import get_agent_service

router = APIRouter(prefix='/api/ai/agent', dependencies=[Depends(local_only)])
SSE_HEARTBEAT_SECONDS = 15.0


def thread(id, account):
    try:
        return get_agent_service().public_thread(id, account_name(account))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None


def execution_account(id, account):
    owner = account_name(account)
    run = get_agent_service().run(id, owner)
    if run.get('library_scope') is None:
        chat_account_name(owner)
    return owner


@router.get('/threads')
def threads(account: str, username: str = '', origin: Literal['chat', 'agent'] | None = None):
    store = get_agent_service().store
    owner = account_name(account)
    records = store.list('agent_thread', owner)
    result = []
    for record in records:
        if record.get('parent_run_id'):
            continue
        if username and record['username'] != username:
            continue
        record.setdefault('origin', 'chat')
        record.setdefault('chat_scope', None)
        if origin is not None and record['origin'] != origin:
            continue
        item = {k: v for k, v in record.items() if k not in ('messages', 'memory')}
        # 列表只返回最新任务的状态，不加载证据或把回答内容带入列表。
        latest = store.get('agent_run', record['latest_run']) if record.get('latest_run') else None
        item['latest_run_status'] = latest.get('status', '') if latest and latest.get('account') == owner and latest.get('thread_id') == record['id'] else ''
        result.append(item)
    return result


@router.post('/threads')
async def create_thread(body: ThreadInput):
    try:
        service = get_agent_service()
        lock = service.ai.account_lifecycle_lock
        while not lock.acquire(blocking=False):
            await asyncio.sleep(.05)
        try:
            owner = account_name(body.account) if body.library_scope is not None else chat_account_name(body.account)
            # 删除完成后再验证账号；重新导入的同名账号可恢复写入。
            service.ai.deleted_accounts.discard(owner)
            service.store.revoked_accounts.discard(owner)
            return await service.create_thread(owner, body.username, body.title, origin=body.origin, chat_scope=body.chat_scope,
                library_scope=body.library_scope.model_dump() if body.library_scope is not None else None)
        finally:
            lock.release()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get('/threads/{id}')
def get_thread(id: str, account: str):
    return thread(id, account)


@router.patch('/threads/{id}')
async def edit_thread(id: str, account: str, body: ThreadUpdate):
    record = thread(id, account)
    try:
        return await get_agent_service().edit_thread(id, record['account'], **body.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.delete('/threads/{id}')
async def delete_thread(id: str, account: str):
    record = thread(id, account)
    await get_agent_service().delete_thread(id, record['account'])
    return {'status': 'success'}


@router.post('/threads/{id}/messages')
async def submit(id: str, account: str, body: TurnInput):
    record = thread(id, account)
    if record.get('library_scope') is None:
        chat_account_name(record['account'])
    try:
        run = await get_agent_service().submit(id, record['account'], body.model_dump())
        return get_agent_service().public_run(run['id'], record['account'])
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from None


@router.get('/runs/{id}')
def get_run(id: str, account: str):
    try:
        return get_agent_service().public_run(id, account_name(account))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None


@router.post('/runs/{id}/restart')
async def restart_run(id: str, account: str, body: RestartInput):
    service = get_agent_service()
    try:
        owner = execution_account(id, account)
        run = await service.restart(id, owner, body.request_id)
        return service.public_run(run['id'], owner)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post('/runs/{id}/resume')
async def resume_run(id: str, account: str):
    service = get_agent_service()
    try:
        account = execution_account(id, account)
        if service.run(id, account).get('engine_version') != 3:
            raise HTTPException(409, {'code': 'legacy_restart_required', 'message': '旧任务不能继续，请使用新引擎重新运行。',
                'restart_url': f'/api/ai/agent/runs/{id}/restart'})
        await service.resume(id, account)
        return service.public_run(id, account)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post('/runs/{id}/{action}')
async def run_action(id: str, action: str, account: str):
    service = get_agent_service()
    try:
        account = account_name(account)
        service.run(id, account)
        if action == 'stop':
            await service.stop_run(id, account)
        elif action in ('continue', 'resume'):
            execution_account(id, account)
            if service.run(id, account).get('engine_version') != 3:
                raise HTTPException(409, {'code': 'legacy_restart_required', 'message': '旧任务不能继续，请使用新引擎重新运行。',
                    'restart_url': f'/api/ai/agent/runs/{id}/restart'})
            await service.resume(id, account)
        else:
            raise HTTPException(404, '操作不存在')
        return service.public_run(id, account)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get('/runs/{id}/materials')
def materials(id: str, account: str, kind: str = Query('sources', pattern='^(sources|findings|statistics|notes)$'),
              offset: int = Query(0,ge=0), limit: int = Query(20,ge=1,le=100),
              query: str = Query('',max_length=500), version: int | None = None):
    try:
        return get_agent_service().material_page(id,account_name(account),kind,offset,limit,query,version)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from None


@router.get('/runs/{id}/context-compactions/{job_id}')
def context_compaction(id: str, job_id: str, account: str, version: int = Query(..., ge=1)):
    service = get_agent_service()
    try:
        run = service.run(id, account_name(account))
        # 历史版本也可阅读，但必须来自同一个账号、任务及确切的压缩记录。
        if version > run['version']:
            raise ValueError('上下文压缩记录不存在。')
        record = service.workspace.get(id, version, 'context:job:' + job_id)
        if not record or record.get('id') != job_id:
            raise ValueError('上下文压缩记录不存在。')
        summary = record.get('summary') if record.get('status') == 'completed' else None
        if summary is None and record.get('status') == 'completed':
            # 旧记录只能回读同一次快照，不能把后一次摘要误当作前一次结果。
            event = service.workspace.get(id, version, 'context:event') or {}
            if event.get('context_revision') == job_id:
                content = event.get('summary_message', {}).get('data', {}).get('content', '')
                if isinstance(content, str) and '<compacted-summary>\n' in content:
                    summary = content.split('<compacted-summary>\n', 1)[1].split('\n</compacted-summary>', 1)[0]
        return {key: record.get(key) for key in ('id', 'status', 'reason', 'before', 'after', 'model_window', 'created_at', 'finished_at')} | {
            'version': version, 'summary': summary, 'summary_available': bool(summary)}
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None


@router.get('/runs/{id}/subtasks')
def subtasks(id: str, account: str, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
             version: int | None = None):
    try:
        return get_agent_service().subtasks.page(id, account_name(account), offset, limit, version)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get('/runs/{id}/subtasks/{task_id}')
def subtask_result(id: str, task_id: str, account: str, version: int,
                   offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
    service = get_agent_service()
    try:
        parent = service.authorize_material(id, account_name(account), version)
        with service.store.connection() as db:
            row = db.execute('SELECT body FROM agent_subtask WHERE id=? AND parent_id=? AND account=? AND version=?',
                             (task_id, id, parent['account'], version)).fetchone()
        if not row:
            raise ValueError('子任务不存在或已不属于当前范围。')
        job = json.loads(row[0])
        if not job.get('result_handle'):
            return {'items': [], 'total': 0, 'offset': offset, 'has_more': False, 'version': version}
        child = service.run(job['child_run_id'], parent['account'])
        return service.workspace.page(child['id'], child['version'], 'finding', offset, limit) | {'version': version}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get('/runs/{id}/materials/{source:path}')
def material(id: str, source: str, account: str, offset: int = Query(0, ge=0), version: int | None = None):
    service = get_agent_service()
    offset = offset if isinstance(offset, int) else (int(getattr(offset, 'default', 0)) if hasattr(offset, 'default') else 0)
    version = version if isinstance(version, int) else (getattr(version, 'default', None) if hasattr(version, 'default') else None)
    try:
        run = service.authorize_material(id, account_name(account), version)
        value = run['evidence'].get(source)
        if value:
            text = value['text'][offset:offset + 6000]
            return service.public_source(value, run['account']) | {
                'text': text,
                'offset': offset,
                'next_offset': offset + len(text) if offset + len(text) < len(value['text']) else None,
            }
        # Check virtual filesystem /notes/
        norm_path = source if source.startswith('/') else '/' + source
        file_piece = service.workspace.get(id, run['version'], 'file:' + norm_path)
        if file_piece:
            content = file_piece.get('content', '')
            chunk = content[offset:offset + 6000]
            return {
                'source': norm_path,
                'name': PurePosixPath(norm_path).name,
                'text': chunk,
                'offset': offset,
                'next_offset': offset + len(chunk) if offset + len(chunk) < len(content) else None,
                'is_file': True,
            }
        raise ValueError('来源不存在或已不在读取范围。')
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None


@router.get('/events')
async def events(
    request: Request,
    account: str,
    after: int | None = None,
    last_event_id: int | None = Query(None, alias='last-event-id'),
    last_event_id_alt: int | None = Query(None, alias='last_event_id'),
):
    account = account_name(account)
    store = get_agent_service().store
    header_val = request.headers.get('last-event-id')

    def _to_int(val):
        if val is None or hasattr(val, 'default'):
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    explicit_cursor = _to_int(after)
    if explicit_cursor is None:
        explicit_cursor = _to_int(last_event_id)
    if explicit_cursor is None:
        explicit_cursor = _to_int(last_event_id_alt)
    header_cursor = _to_int(header_val)

    if explicit_cursor is not None and header_cursor is not None:
        cursor = max(explicit_cursor, header_cursor)
    elif explicit_cursor is not None:
        cursor = max(0, explicit_cursor)
    elif header_cursor is not None:
        cursor = max(0, header_cursor)
    else:
        cursor = store.latest_event_id()
    async def stream():
        nonlocal cursor
        # 首次业务事件前也固定重连起点；仅 id 的块不触发前端 onmessage。
        yield f'id: {cursor}\n\n'
        while not await request.is_disconnected():
            # 先记录等待点再读取，事件即使落在查询与等待之间也不会漏掉唤醒。
            revision = store.event_revision(account)
            delivered = False
            while True:
                batch = store.events(after=cursor, account=account)
                if not batch:
                    break
                for event in batch:
                    cursor = event['id']
                    if event['kind'] == 'agent':
                        delivered = True
                        yield f"id: {event['id']}\ndata: {json.dumps(event['body'], ensure_ascii=False)}\n\n"
                # 每批最多 100 条；继续排空，避免高频回答积压到下一次心跳。
                if len(batch) < 100:
                    break
            if delivered:
                continue
            changed, _ = await asyncio.to_thread(
                store.wait_for_event, account, revision, SSE_HEARTBEAT_SECONDS,
            )
            if not changed:
                yield ': heartbeat\n\n'
    return StreamingResponse(stream(), media_type='text/event-stream', headers={
        'Cache-Control': 'no-cache, no-transform',
        'Connection': 'keep-alive',
        'X-Accel-Buffering': 'no',
    })
