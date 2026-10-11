"""本地检索任务、账号范围与设备状态的统一服务。"""
from ..ai.diagnostics import observed, event as diagnostic_event, context as diagnostic_context, new_id, executor_call, failures
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
import hashlib
from pathlib import Path
import threading
import time
import uuid

from ..ai.storage import AIStore
from ..app_paths import get_data_dir, get_output_dir, get_output_databases_dir
from ..account_workers import account_to_thread
from ..snapshot_registry import account_work, create_account_task, SnapshotRegistryError
from .catalog import model_dir, model_spec
from .downloads import ModelDownloads
from .index import SemanticIndex, LiteralIndexNotReady, make_chunks, fuse, retrieval_text
from .inference import LocalInference, InferenceFailure
from .progressive import ProgressiveIndex, reading_segments, committed_coverage, coverage_complete
from .totals import MessageTotals


DEFAULTS = {'enabled': False, 'model': None, 'usernames': [], 'days': 90,
            'start': None, 'end': None, 'device': 'auto', 'device_id': 0, 'device_identity': None, 'auto_update': True,
            'read_batch_size': 0, 'agent_global': False}

# 进度事件里体积大且很少变化、或可从权威记录重建的字段，不随每次进度写入事件表。
EVENT_OMITTED_FIELDS = frozenset({'config', 'coverage', 'segments', 'read_starts'})


class RetrievalFailure(RuntimeError):
    def __init__(self, message, code, status_code=503, coverage=None):
        super().__init__(message)
        self.code, self.status_code, self.coverage = code, status_code, coverage


def validate_device_config(cfg):
    if cfg['device'] not in {'auto', 'cpu', 'cuda', 'dml'}:
        raise ValueError('检索设备配置无效')
    if type(cfg['device_id']) is not int or not 0 <= cfg['device_id'] <= 32:
        raise ValueError('检索 CUDA 设备序号无效')
    identity = cfg['device_identity']
    if identity is not None and (not isinstance(identity, str) or not identity.strip()):
        raise ValueError('检索设备身份配置无效')
    if cfg['device'] == 'dml' and identity is None:
        raise ValueError('DirectML 必须指定设备身份')
    if cfg['device'] in {'auto', 'cpu'} and identity is not None:
        raise ValueError('自动或 CPU 模式不能指定 GPU 设备身份')


class LocalSearch(ProgressiveIndex, MessageTotals):
    def __init__(self, root=None, model_root=None, reader=None, engine=None):
        self.root = Path(root or get_output_dir() / 'local_search')
        self.store = AIStore(self.root)
        self.engine = engine or LocalInference(callback=lambda status: self.store.event(status.get('account', ''), 'local_search_device', status,
            unique_key='local_search_device:' + status.get('account', ''), replace=True))
        self.downloads = ModelDownloads(model_root or get_data_dir() / 'local_search_models', self.store, self.engine)
        from .gpu import GPUComponent
        self.gpu = GPUComponent(self.downloads.root.parent / 'local_search_gpu', self.store, self.engine)
        self.dml = GPUComponent(self.downloads.root.parent / 'local_search_gpu', self.store, backend='dml')
        from ..local_acceleration import LocalAcceleration
        self.acceleration = LocalAcceleration(self.store, {'cuda': self.gpu, 'dml': self.dml})
        if isinstance(self.engine, LocalInference):
            self.engine.acceleration = self.acceleration
        self.reader = reader
        self.jobs, self.cancelled, self.revoked, self.queries = {}, set(), set(), {}
        self.fts_jobs, self.fts_cancelled = {}, set()
        self.restarting=set()
        self.foreground_queries = 0
        self.queue_lock = asyncio.Lock()
        self.model_lock = asyncio.Lock()
        self.loop_task = None

    def config(self, account):
        saved = self.store.get('config', account)
        if saved is not None and not isinstance(saved, dict):
            raise ValueError('检索配置损坏')
        cfg = {**DEFAULTS, **(saved if saved is not None else {}), 'account': account}
        validate_device_config(cfg)
        return cfg

    def index(self, account):
        safe = hashlib.sha256(account.encode()).hexdigest()
        return SemanticIndex(self.root / 'indexes' / (safe + '.sqlite3'))

    def status(self, account=None):
        event_cursor = self.store.latest_event_id()
        cfg = self.config(account) if account else None
        jobs = self.store.list('index_job', account, limit=5) if account else []
        device = self.acceleration.runtime_status('search', account, model_spec(cfg['model'])) if cfg and cfg['model'] else {'actual_device': None, 'using_fallback': False, 'reason': ''}
        result = {'config': cfg, 'models': self.downloads.models(), 'device': device, 'jobs': jobs, 'event_cursor': event_cursor,
                  'message_total': self.store.get('index_message_total', jobs[0]['id']) if jobs else None,
                  'gpu': {**self.acceleration.component_statuses()['cuda'], 'failed': self.engine.gpu_failed}, 'audit': self.store.list('local_usage', account, limit=20) if account else []}
        if account:
            index = self.index(account)
            result['index_bytes'] = index.path.stat().st_size
            active = cfg.get('active') or {}
            result['index_stats'] = index.stats(active['generation']) if active.get('generation') else {'messages': 0, 'chunks': 0}
            result['literal_index'] = {**index.fts_status(active.get('generation')),
                                       'migration': self.store.get('fts_migration', account)}
        return result

    def start_fts_migration(self, account, resume=False):
        """排队即持有账号租约；旧向量完全复用，错误只由显式恢复重新尝试。"""
        cfg = self.config(account)
        if not cfg['enabled'] or account in self.revoked:
            return None
        task = self.fts_jobs.get(account)
        if task and not task.done():
            return task
        state = self.store.get('fts_migration', account) or {}
        if not resume and state.get('status') in {'paused', 'error'}:
            return None
        self.fts_cancelled.discard(account)
        task = create_account_task(get_output_databases_dir() / account, self.migrate_fts, account)
        self.fts_jobs[account] = task
        return task

    async def migrate_fts(self, account):
        account_dir = get_output_databases_dir() / account
        state = {'account': account, 'status': 'running', 'processed': 0}
        def check():
            if account in self.fts_cancelled or account in self.revoked:
                raise InferenceFailure('字面索引升级已暂停', 'cancelled')
        def save(**changes):
            state.update(changes, updated=time.time())
            if account not in self.revoked:
                self.store.put('fts_migration', state, id=account, account=account)
        try:
            check()
            index = await account_to_thread(account_dir, self.index, account)
            coverage = await account_to_thread(account_dir, index.fts_status)
            save(coverage=coverage)
            while not coverage['complete']:
                check()
                await self.yield_to_queries(check)
                generation = (self.config(account).get('active') or {}).get('generation')
                batch = await account_to_thread(account_dir, index.backfill_fts, generation, 500, check)
                coverage = {key: batch[key] for key in ('complete', 'pending', 'generation')}
                save(processed=state['processed'] + batch['processed'], coverage=coverage)
                await asyncio.sleep(0)
            save(status='complete')
        except InferenceFailure as error:
            save(status='paused' if error.category == 'cancelled' else 'error', error=str(error))
            if error.category != 'cancelled':
                failures.report('search.fts.migration.' + account, error)
        except Exception as error:
            save(status='error', error='字面索引升级失败，请恢复索引任务或重建索引', error_type=type(error).__name__)
            failures.report('search.fts.migration.' + account, error)

    @observed('search.configure', id_field='task_id')
    async def configure(self, account, values):
        account_dir = get_output_databases_dir() / account
        with account_work(account_dir):
            async with self.model_lock:
                return await self._configure(account,values)

    async def _configure(self, account, values):
        account_dir = get_output_databases_dir() / account
        self.revoked.discard(account)
        self.store.revoked_accounts.discard(account)
        old = self.config(account)
        cfg = {**old, **values, 'account': account}
        validate_device_config(cfg)
        if cfg['model'] is not None: model_spec(cfg['model'])
        if cfg['enabled'] and not self.downloads.available(cfg['model']):
            raise ValueError('请先下载并选择可用的本地模型')
        # 重复保存相同设置不使断点失效，也不打断后台任务。
        if all(cfg.get(key) == old.get(key) for key in DEFAULTS):
            return old
        cfg['revision'] = old.get('revision', 0) + 1
        self.store.put('config', cfg, id=account, account=account)
        # 范围或设备变更后，旧任务不得继续提交过期配置。
        migration = self.store.get('fts_migration', account) or {}
        await self.pause_account(account)
        await account_to_thread(account_dir, self.clear_message_plans, account)
        if cfg.get('active'):
            start=cfg['start'] if cfg['start'] is not None else max(0,int(time.time())-cfg['days']*86400) if cfg['days'] else 0
            await account_to_thread(account_dir, self.index(account).prune,cfg['active']['generation'],cfg['usernames'],start,cfg['end'] or 2**53)
            # 清理后同步实际覆盖范围，防止先缩小再扩大时把已删除内容误判为可复用。
            active = cfg['active']
            cfg['active'] = {**active, 'usernames': [u for u in active.get('usernames', []) if u in cfg['usernames']],
                             'start': max(active.get('start', start), start),
                             'end': min(active.get('end', cfg['end'] or 2**53), cfg['end'] or 2**53)}
            if 'coverage' in active:
                cfg['active']['coverage'] = committed_coverage({'config': cfg, 'generation': active['generation'],
                    'start': cfg['active']['start'], 'end': cfg['active']['end']})
            self.store.put('config', cfg, id=account, account=account)
        self.queries.clear()
        # configure 也由后台范围更新调用，只恢复本次配置临时暂停的迁移。
        stopped_migration = self.store.get('fts_migration', account) or {}
        self.start_fts_migration(account, resume=migration.get('status') not in {'paused', 'error'}
                                 and stopped_migration.get('status') != 'error')
        self.store.event(account, 'local_search_settings',
                         {'account': account, 'model': cfg['model'], 'revision': cfg['revision']},
                         unique_key='local_search_settings:' + account, replace=True)
        return cfg

    def update(self, job, **changes):
        job.update(changes, updated=time.time())
        if job['id'] in self.restarting: job['resume_on_start']=True
        if job['account'] in self.revoked: return
        self.store.put('index_job', job, id=job['id'], account=job['account'])
        # 事件表只保留每个任务的最新进度；完整快照以 records 表为准。
        progress = {key: value for key, value in job.items() if key not in EVENT_OMITTED_FIELDS}
        self.store.event(job['account'], 'local_search_index', progress, unique_key=f'index_job:{job["id"]}', replace=True)

    def enrichment_version(self, account):
        """只检查本地提取缓存，不触发媒体分析或网络访问。"""
        import sqlite3
        values = []
        path = get_output_dir() / 'ai' / 'ai.sqlite3'
        if path.exists():
            with sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True) as db:
                values.append(db.execute("SELECT coalesce(max(updated),0) FROM records WHERE kind='local_media_text' AND account=?", (account,)).fetchone()[0])
        from ..chat_helpers import _resolve_account_dir
        path = _resolve_account_dir(account) / '_cache/voice_transcripts.sqlite3'
        if path.exists():
            with sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True) as db:
                values.append(db.execute('SELECT coalesce(max(updated_at),0) FROM transcript').fetchone()[0])
        return values

    def local_text(self, account, messages):
        import sqlite3, json
        from ..ai.agent_tools import attachment_message_signature
        path = get_output_dir() / 'ai' / 'ai.sqlite3'
        if not path.exists(): return
        with sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True) as db:
            for m in messages:
                if m['kind'] not in {'file', 'image'}: continue
                id = hashlib.sha256(f"{account}:{m['username']}:{m['anchor']}".encode()).hexdigest()
                row = db.execute("SELECT body FROM records WHERE kind='local_media_text' AND id=? AND account=?", (id,account)).fetchone()
                if row:
                    cached = json.loads(row[0])
                    if not isinstance(cached, dict) or not isinstance(cached.get('text'), str):
                        raise ValueError('附件正文缓存损坏')
                    # 历史 local_media_text 只保存本地文档解析文字，未写入视觉描述。
                    method = cached.get('extraction_method', 'document_text')
                    if method not in {'document_text', 'local_ocr'} or (m['kind'] == 'image' and method != 'local_ocr'):
                        raise ValueError(f'附件正文缓存来源无效：{method}')
                    signature = cached.get('message_signature')
                    if method == 'local_ocr' and not isinstance(signature, dict):
                        raise ValueError('本地 OCR 缓存缺少原消息身份')
                    if signature is not None and not isinstance(signature, dict):
                        raise ValueError('附件正文缓存原消息身份损坏')
                    current_signature = attachment_message_signature(m)
                    if signature is not None and signature != current_signature:
                        diagnostic_event('search.local_text.invalidated', account=account, username=m['username'], anchor=m['anchor'])
                        continue
                    m['local_attachment_text'] = cached['text']
                    m['local_attachment_method'] = method
                    m['local_attachment_provenance'] = {'account': account, 'message_signature': current_signature,
                        'extraction_method': method, 'file_hash': cached['file_hash'], 'version': cached['version'],
                        'complete': cached.get('complete') is True, 'status': cached.get('status', 'partial')}
                    if method == 'local_ocr':
                        m['local_attachment_provenance'].update(ocr_units=cached['ocr_units'], ocr_engine=cached['ocr_engine'])

    @observed('search.build', id_field='task_id')
    async def build(self, account, rebuild=False, incremental=True):
        account_dir = get_output_databases_dir() / account
        with account_work(account_dir):
            # 后台调度和前台提问可能同时触发；创建任务与配置更新共用锁。
            async with self.model_lock:
                return await self._build(account, rebuild, incremental)

    async def _build(self, account, rebuild=False, incremental=True):
        account_dir = get_output_databases_dir() / account
        cfg = self.config(account)
        if not cfg['enabled']: raise ValueError('请先启用本地语义检索')
        if not cfg['usernames']: raise ValueError('请选择需要建立索引的聊天')
        if not self.downloads.available(cfg['model']): raise ValueError('检索模型尚未下载完成')
        for id, task in self.jobs.items():
            job = self.store.get('index_job', id)
            if not task.done() and job and job['account'] == account:
                if rebuild:
                    self.start_fts_migration(account, resume=True)
                return job
        end = min(cfg['end'] or int(time.time()), int(time.time()))
        start = cfg['start'] if cfg['start'] is not None else max(0, end - cfg['days'] * 86400) if cfg['days'] else 0
        active = cfg.get('active') or {}
        new_generation = rebuild or active.get('model') != cfg['model'] or not active.get('generation')
        generation = uuid.uuid4().hex if new_generation else active['generation']
        enrichment = await account_to_thread(account_dir, self.enrichment_version, account)
        # 增量保留同秒和短期补写窗口；旧转写/附件变化或每天一次校对重读范围。
        recent_only = incremental and not new_generation and enrichment == active.get('enrichment') and time.time()-active.get('reconciled',0)<86400
        # 按聊天判断覆盖范围；新增聊天或向前扩展历史必须补齐，设备设置不影响复用。
        read_starts = {username: max(start, active.get('end', start)-600)
                       if recent_only and username in active.get('usernames', []) and start >= active.get('start', start)
                       else start for username in cfg['usernames']}
        reason = ('rebuild' if rebuild else 'initial' if new_generation else
                  'manual_check' if not incremental else 'enrichment' if enrichment != active.get('enrichment') else
                  'reconcile' if not recent_only else 'incremental')
        read_start = min(read_starts.values())
        job = {'id': uuid.uuid4().hex, 'account': account, 'config': cfg, 'generation': generation,
               'trace_id': diagnostic_context.get().get('trace_id') or new_id(),
               'read_start': read_start, 'read_starts': read_starts, 'mode': reason,
               'incremental': recent_only, 'enrichment': enrichment,
               'start': start, 'end': end, 'chat_index': 0, 'offset': 0, 'processed': 0,
               'embedded': 0, 'unchanged': 0, 'status': 'queued', 'stage': 'queued', 'started': time.time(), 'warning': '', 'error': ''}
        if cfg.get('agent_global'):
            job['segments'] = reading_segments(cfg['usernames'], read_starts, end)
            job['coverage'] = {}
        self.update(job)
        self.jobs[job['id']] = create_account_task(account_dir, self.run, job)
        self.start_fts_migration(account, resume=rebuild)
        return job

    @observed('search.resume', id_field='task_id')
    async def resume(self, account, id, *, resume_migration=True):
        account_dir = get_output_databases_dir() / account
        with account_work(account_dir):
            job = self.store.get('index_job', id)
            if not job or job['account'] != account: raise ValueError('任务不存在')
            if job['config']['revision'] != self.config(account).get('revision'):
                raise ValueError('配置已改变，请按当前范围重新建立索引')
            if id in self.jobs and not self.jobs[id].done():
                self.start_fts_migration(account, resume=resume_migration)
                return job
            saved = self.index(account).progress(id)
            if saved: job.update(saved)
            self.cancelled.discard(id)
            job.pop('finished', None)
            self.update(job, status='queued', error='')
            self.jobs[id] = create_account_task(account_dir, self.run, job)
            self.start_fts_migration(account, resume=resume_migration)
            return job

    @observed('search.pause_account', id_field='task_id')
    async def pause_account(self, account):
        self.fts_cancelled.add(account)
        migration = self.fts_jobs.get(account)
        if migration and not migration.done():
            await asyncio.gather(migration, return_exceptions=True)
        for id, task in list(self.jobs.items()):
            job = self.store.get('index_job', id)
            if job and job['account'] == account and not task.done():
                self.cancelled.add(id)
                if job['status']=='queued':
                    task.cancel()
                    self.update(job,status='paused',stage='paused',finished=time.time())
                else:
                    self.update(job,stage='pausing')
                await asyncio.gather(task, return_exceptions=True)

    @asynccontextmanager
    async def open_pages(self, account, username, start, end, offset, checkpoint, page_size=0, on_progress=None, on_batch_size=None, cursor=None, frozen=None):
        """在专用线程中顺序推进和关闭游标，避免线程池切换破坏 SQLite 连接。"""
        account_dir = get_output_databases_dir() / account
        with account_work(account_dir):
            from ..ai.messages import iter_message_pages
            from .reading import choose_read_batch_size
            closing = threading.Event()

            def check():
                if closing.is_set():
                    raise InferenceFailure('读取已停止', 'cancelled')
                checkpoint()

            def batch_size():
                size = choose_read_batch_size(page_size)
                if on_batch_size:
                    loop.call_soon_threadsafe(on_batch_size, size)
                return size

            def pages():
                if frozen:
                    plan, segment = frozen
                    position = offset
                    while True:
                        result = plan.page(segment, position, batch_size(), check)
                        position += len(result['messages'])
                        if on_progress:
                            loop.call_soon_threadsafe(on_progress, position)
                        yield result
                        if not result['has_more']:
                            return
                elif self.reader:
                    # 保留可注入的分页数据源，进度仍按事务提交的消息数量计算。
                    position = offset
                    while True:
                        check()
                        result = self.reader(account, username, start, end, position)
                        yield result
                        if not result.get('has_more', False):
                            return
                        position += len(result['messages'])
                else:
                    yield from iter_message_pages(account, username, start, end,
                        page_offset=offset, page_size=batch_size, checkpoint=check,
                        cursor=cursor, emit_cursor=True,
                        on_progress=(lambda count: loop.call_soon_threadsafe(on_progress, count)) if on_progress else None)

            executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='local-search-reader')
            loop = asyncio.get_running_loop()
            stream = pages()
            try:
                yield lambda: executor_call(executor, next, stream, None)
            finally:
                closing.set()
                # Close on the reader thread, after any in-flight SQLite cursor work.
                close_task = executor_call(executor, stream.close)
                def report_close(done):
                    if not done.cancelled() and done.exception() is not None:
                        failures.report('search.reader.close', done.exception())
                close_task.add_done_callback(report_close)
                try:
                    await asyncio.shield(close_task)
                finally:
                    # A second cancellation may end this waiter; the shutdown worker
                    # retains its own account lease until the reader really exits.
                    await account_to_thread(account_dir, executor.shutdown, wait=True)

    @observed('search.run', id_field='task_id', execution=True)
    async def run(self, job):
        account_dir = get_output_databases_dir() / job['account']
        with account_work(account_dir):
            cfg, account = job['config'], job['account']
            plan = None
            def cancelled(): return job['id'] in self.cancelled or account in self.revoked
            def check():
                if cancelled(): raise InferenceFailure('任务已暂停', 'cancelled')
            async with self.queue_lock:
                try:
                    if cfg['device'] != 'cpu' and self.acceleration.detection_task is not None:
                        await self.acceleration.detection_task
                    check()
                    from tokenizers import Tokenizer
                    spec = model_spec(cfg['model'])
                    root = model_dir(self.downloads.root, cfg['model'])
                    tokenizer = Tokenizer.from_file(str(root / 'tokenizer.json'))
                    index = await account_to_thread(account_dir, self.index, account)
                    self.update(job, status='running', read_count=job['processed'], embedded_count=job['embedded'])
                    plan = await self.count_message_total(job, check)
                    segments = job.get('segments')
                    for position in range(job['chat_index'], len(segments) if segments is not None else len(cfg['usernames'])):
                        segment = segments[position] if segments is not None else None
                        username = segment['username'] if segment else cfg['usernames'][position]
                        offset = job['offset'] if position == job['chat_index'] else 0
                        read_start = segment['start'] if segment else job.get('read_starts', {}).get(username, job.get('read_start', job['start']))
                        read_end = segment['end'] if segment else job['end']
                        read_base = job['processed'] - offset

                        def reading_progress(count):
                            # 展示读取中的真实数量，只有事务提交才能推进断点 processed/offset。
                            total = read_base + count
                            if not cancelled() and job['stage'] == 'reading' and total > job.get('read_count', 0):
                                self.update(job, read_count=total)

                        def batch_size_changed(size):
                            if not cancelled() and job.get('read_batch_size_effective') != size:
                                self.update(job, read_batch_size_effective=size)

                        async with self.open_pages(account, username, read_start, read_end, offset, check,
                                page_size=cfg.get('read_batch_size', 0), on_progress=reading_progress,
                                on_batch_size=batch_size_changed, cursor=job.get('cursor') if offset else None,
                                frozen=(plan, position)) as next_page:
                            while True:
                                check()
                                await self.yield_to_queries(check)
                                self.update(job, stage='reading', current_chat=username)
                                page_started = time.monotonic()
                                diagnostic_event('index.page.started', username=username, offset=offset, start=read_start, end=job['end'])
                                result = await next_page()
                                check()
                                messages = result['messages']
                                diagnostic_event('index.page.finished', username=username, offset=offset, count=len(messages), has_more=result.get('has_more',False), duration_ms=(time.monotonic()-page_started)*1000)
                                await self.yield_to_queries(check)
                                for m in messages:
                                    raw = m.get('media') or {}
                                    m['sender_id'] = raw.get('senderUsername') or m.get('sender', '')
                                    m['name'] = result.get('name', username)
                                await account_to_thread(account_dir, self.local_text, account, messages)
                                await self.yield_to_queries(check)
                                self.update(job, stage='organizing', read_count=job['processed'] + len(messages))
                                unchanged = await account_to_thread(account_dir, index.existing, job['generation'], messages)
                                changed = await account_to_thread(account_dir, index.affected_messages, job['generation'], [m for m in messages if m['source'] not in unchanged])
                                chunks = await account_to_thread(account_dir, make_chunks, changed, tokenizer)
                                diagnostic_event('index.page.organized', count=len(changed), unchanged=len(unchanged), chunks=len(chunks))
                                vectors = []
                                last_embedding_update = time.monotonic()
                                if chunks: self.update(job, stage='embedding')
                                for batch_start in range(0, len(chunks), 8):
                                    check()
                                    await self.yield_to_queries(check)
                                    batch = chunks[batch_start:batch_start + 8]
                                    diagnostic_event('index.batch.started', index=batch_start, batch_size=len(batch), strategy=cfg['device'], reason_code='configured')
                                    values = await account_to_thread(account_dir, self.engine.encode, root, spec, [c['text'] for c in batch], cfg['device'], cfg['device_id'], False, cancelled, cfg.get('device_identity'), account)
                                    diagnostic_event('index.batch.finished', index=batch_start, count=len(values), actual_device=self.engine.status.get('actual_device'))
                                    vectors.extend(values)
                                    if len(vectors) == len(chunks) or time.monotonic() - last_embedding_update >= 0.25:
                                        self.update(job, embedded_count=job['embedded'] + len(vectors))
                                        last_embedding_update = time.monotonic()
                                check()
                                more = result.get('has_more', False)
                                next_job = {**job, 'chat_index': position if more else position + 1,
                                            'offset': offset + len(messages) if more else 0,
                                            'cursor': result.get('cursor') if more else None,
                                            'processed': job['processed'] + len(messages), 'embedded': job['embedded'] + len(chunks),
                                            'unchanged': job.get('unchanged', 0) + len(unchanged),
                                            'warning': result.get('warning', ''), 'source': result.get('source', 'snapshot')}
                                next_job['coverage'] = {**job.get('coverage', {}), str(position): {
                                    'username': username, 'start': read_start, 'end': read_end,
                                    'last_time': result.get('cursor', {}).get('time') if result.get('cursor') else None,
                                    'processed': offset + len(messages), 'complete': not more,
                                    'warning': result.get('warning', '')}}
                                self.update(job, stage='saving')
                                await account_to_thread(account_dir, index.commit, job['generation'], changed, chunks, vectors, next_job, check)
                                job.update(next_job)
                                self.publish_partial(job)
                                self.update(job)
                                if not more: break
                                offset += len(messages)
                    check()
                    frozen_total = (await account_to_thread(account_dir, plan.metadata))['total']
                    if job['processed'] != frozen_total:
                        raise InferenceFailure('本轮消息清单与已保存数量不一致，已保留进度，请重试。', 'count_mismatch')
                    current = self.config(account)
                    if current.get('revision') != cfg.get('revision'): raise InferenceFailure('配置已更新', 'cancelled')
                    # 完成清理、范围约束和统计后才发布成功状态。
                    await account_to_thread(account_dir, index.prune, job['generation'], cfg['usernames'], job['start'], job['end'])
                    stats = await account_to_thread(account_dir, index.stats, job['generation'])
                    coverage = committed_coverage(job) if cfg.get('agent_global') else job.get('coverage', {})
                    current['active'] = {'generation': job['generation'], 'model': cfg['model'], 'start': job['start'],
                                         'end': job['end'], 'usernames': cfg['usernames'], 'updated': time.time(), 'source': job.get('source'),
                                         'revision': cfg['revision'], 'enrichment': job.get('enrichment'),
                                         'coverage': coverage,
                                         'partial': cfg.get('agent_global', False) and not coverage_complete(coverage, cfg['usernames'], job['start'], job['end']),
                                         'reconciled': cfg.get('active',{}).get('reconciled',time.time()) if job.get('incremental') else time.time()}
                    self.store.put('config', current, id=account, account=account)
                    await account_to_thread(account_dir, index.clear, job['generation'])
                    self.update(job, status='done', stage='done', index_stats=stats, finished=time.time())
                    try:
                        await account_to_thread(account_dir, plan.discard)
                    except OSError as error:
                        failures.report('search.plan.cleanup', error)
                except InferenceFailure as error:
                    diagnostic_event('index.execution.interrupted' if error.category=='cancelled' else 'index.execution.failed',
                                     level=logging.INFO if error.category=='cancelled' else logging.ERROR, error=error)
                    self.update(job, status='paused' if error.category == 'cancelled' else 'error', error=str(error), finished=time.time())
                except Exception as error:
                    diagnostic_event('index.execution.failed', level=logging.ERROR, error=error)
                    self.update(job, status='error', error='索引处理失败，已保留进度，请检查数据源和模型后重试', error_type=type(error).__name__, finished=time.time())
                finally:
                    if account in self.revoked:
                        await account_to_thread(account_dir, self.clear_message_plans, account)
                    self.store.put('local_usage', {'kind': 'index', 'account': account, 'model': cfg['model'], 'status':job['status'],
                        'messages': job['processed'], 'chunks': job['embedded'], 'seconds': time.time()-job['started'],
                        **self.acceleration.runtime_status('search', account, model_spec(cfg['model']))},id=job['id'],account=account)

    @observed('search.hybrid', id_field='task_id')
    async def hybrid(self, account, keyword, q, usernames, start=None, end=None, sender=None, kinds=None, offset=0, limit=50, ticket=None, strict_paging=False, paging_context=None):
        account_dir = get_output_databases_dir() / account
        with account_work(account_dir):
            cfg = self.config(account)
            active = cfg.get('active') or {}
            allowed = set(cfg['usernames']) & set(active.get('usernames', [])) & set(usernames)
            key = (account, cfg.get('revision'), cfg['enabled'], active.get('generation'), q,
                   tuple(sorted(usernames)), tuple(sorted(allowed)), start, end, sender,
                   tuple(sorted(kinds or [])), paging_context)
            reset_search = False
            if strict_paging and offset and not ticket:
                raise RetrievalFailure('混合检索续页缺少搜索票据，请重新开始搜索；已取得证据仍保留', 'search_ticket_required', 409)
            if offset and not ticket:
                ticket = next((k for k,v in reversed(list(self.queries.items())) if v['key']==key and v['expires']>time.time()),None)
            if ticket:
                cached = self.queries.get(ticket)
                if cached and cached['key'] == key and cached['expires'] > time.time():
                    diagnostic_event('search.ticket.hit', ticket_id=ticket, offset=offset, cached=True)
                    return {**cached['response'], 'hits': cached['hits'][offset:offset+limit],
                            'offset': offset, 'limit': limit,
                            'hasMore': offset+limit < len(cached['hits']), 'resetSearch':False}
                if strict_paging:
                    raise RetrievalFailure('搜索票据已失效、过期或检索条件已改变，请重新开始搜索；已取得证据仍保留', 'search_ticket_invalid', 409)
                reset_search = bool(offset)
                diagnostic_event('search.ticket.expired', offset=offset, cached=False)
                offset = 0
            if not cfg['enabled'] or not active or not allowed:
                if strict_paging:
                    raise RetrievalFailure('所选范围的本地语义索引尚未就绪，请检查索引状态', 'semantic_index_not_ready', 409)
                diagnostic_event('search.keyword.fallback', reason_code='disabled' if not cfg['enabled'] else 'scope_not_indexed')
                return {**keyword, 'retrievalMode': 'keyword', 'coverage': {'message': '本地语义索引尚未覆盖所选范围，当前展示关键词结果'}}
            if strict_paging:
                jobs = self.store.list('index_job', account, limit=1)
                job = jobs[0] if jobs and jobs[0]['config'].get('revision') == cfg.get('revision') else None
                if job and job['status'] == 'error':
                    raise RetrievalFailure('本地语义索引构建失败，请检查索引任务后重试', 'semantic_index_error')
                if (job and job['status'] in {'queued', 'running'}) or active.get('partial'):
                    raise RetrievalFailure('本地语义索引尚在构建或尚未完成，请完成索引后重试', 'semantic_index_building', 409)
            began = time.monotonic()
            snapshot_keyword, committed_keyword_hits = keyword, []
            self.foreground_queries += 1
            try:
                scope_start = cfg['start'] if cfg['start'] is not None else max(0, int(time.time())-cfg['days']*86400) if cfg['days'] else 0
                query_start, query_end = max(start or 0, scope_start), min(end if end is not None else 2**53, cfg['end'] if cfg['end'] is not None else 2**53)
                index = await account_to_thread(account_dir, self.index, account)
                def as_hit(m):
                    text = retrieval_text(m)
                    hit = {**m.get('media', {}), 'id': m['anchor'], 'anchorId': m['anchor'], 'username': m['username'],
                            'conversationName': m.get('name', m['username']), 'senderDisplayName': m['sender'],
                            'senderUsername': m.get('sender_id', m['sender']), 'createTime': m['time'],
                            # 索引正文已经包含标题、引用和转写；AI 适配器不能再次拼接这些字段。
                            'content': m['text'], 'aiText': text, 'snippet': text, 'renderType': m['kind']}
                    if m.get('local_attachment_provenance'):
                        hit.update(originalAiText=m['text'], localAttachmentText=m['local_attachment_text'],
                                   localAttachmentProvenance=m['local_attachment_provenance'])
                    return hit
                literal = await account_to_thread(account_dir, index.keyword, active['generation'], q, sorted(allowed), query_start, query_end, sender, kinds, 200)
                # 渐进索引里的新消息可能尚未进入旧全文索引，原文命中必须独立于向量召回。
                committed_keyword_hits = [as_hit(m) for m in literal]
                keyword = {**keyword, 'hits': fuse([*committed_keyword_hits, *keyword.get('hits', [])], [])}
                spec = model_spec(active['model'])
                root = model_dir(self.downloads.root, active['model'])
                def query_cancelled():
                    return account in self.revoked or self.config(account).get('revision') != cfg.get('revision')
                vectors = await account_to_thread(account_dir, self.engine.encode, root, spec, [q], cfg['device'], cfg['device_id'], True, query_cancelled, cfg.get('device_identity'), account)
                if query_cancelled():
                    raise InferenceFailure('检索配置已改变，当前查询已停止', 'cancelled')
                device_status = self.acceleration.runtime_status('search', account, spec)
                rows = await account_to_thread(account_dir, index.search, active['generation'], vectors[0], sorted(allowed), query_start, query_end, sender, kinds, 200)
                semantic = [as_hit(row['message']) for row in rows]
                # 同一片段内的消息共享向量分数；直接匹配原文的消息应先于相邻闲聊。
                needle = q.strip().casefold()
                if needle:
                    semantic.sort(key=lambda hit: needle not in hit['aiText'].casefold())
                hits = fuse(keyword.get('hits', []), semantic)
                diagnostic_event('search.recall.finished', keyword_count=len(keyword.get('hits',[])), semantic_count=len(semantic), returned=len(hits), actual_device=self.engine.status.get('actual_device'))
                coverage = {'message': '语义结果来自已建立的本地索引；新消息、未解析图片和范围外历史可能尚未覆盖。',
                            'start': active['start'], 'end': active['end'], 'updated': active['updated'],
                            'partial': bool(active.get('partial')) or bool(set(usernames)-allowed) or len(rows) >= 200 or len(literal) >= 200,
                            'ranges': active.get('coverage', {}), 'source': active.get('source')}
                ticket = uuid.uuid4().hex
                response = {**keyword, 'retrievalMode': 'hybrid', 'requestedRetrievalMode': 'hybrid',
                            'offset': offset, 'limit': limit, 'coverage': coverage, 'total': len(hits), 'searchTicket': ticket,
                            'device': device_status, 'status': 'success', 'resetSearch':reset_search}
                if query_cancelled():
                    raise InferenceFailure('检索配置已改变，当前查询已停止', 'cancelled')
                self.queries = {k:v for k,v in self.queries.items() if v['expires'] > time.time()}
                if len(self.queries) >= 32: self.queries.pop(next(iter(self.queries)))
                self.queries[ticket] = {'key': key, 'response': response, 'hits': hits, 'expires': time.time()+600}
                self.store.put('local_usage', {'kind':'search','account':account,'model':active['model'],'candidates':len(hits),
                    'seconds':time.monotonic()-began,**device_status}, account=account)
                return {**response, 'hits': hits[offset:offset+limit], 'hasMore': offset+limit<len(hits)}
            except SnapshotRegistryError:
                raise
            except LiteralIndexNotReady as error:
                migration = self.store.get('fts_migration', account) or {}
                if migration.get('status') == 'error':
                    raise RetrievalFailure(migration['error'], 'literal_index_error', 503, error.coverage) from error
                if migration.get('status') == 'paused':
                    raise RetrievalFailure('字面索引升级已暂停，请恢复索引任务继续升级', 'literal_index_paused', 409, error.coverage) from error
                raise RetrievalFailure(str(error), error.code, 409, error.coverage) from error
            except Exception as error:
                must_stop = strict_paging or cfg['device'] in {'cuda', 'dml'} or (isinstance(error, InferenceFailure) and error.category == 'cancelled')
                diagnostic_event('search.retrieval.failed' if must_stop else 'search.keyword.fallback',
                    level=logging.ERROR if must_stop else logging.WARNING, error=error, reason_code='semantic_failed')
                self.store.put('local_usage',{'kind':'search','account':account,'model':active.get('model'),'status':'error',
                    'seconds':time.monotonic()-began,'error_type':type(error).__name__},account=account)
                if must_stop:
                    raise RetrievalFailure(f'本地混合检索失败：{error}', 'semantic_search_failed') from error
                # 旧全文结果已经按 offset 分页，不能再对它重复切片。
                fallback_hits = fuse([*committed_keyword_hits[offset:offset+limit], *snapshot_keyword.get('hits', [])], [])
                return {**keyword, 'hits': fallback_hits[:limit],
                        'total': max(snapshot_keyword.get('total', 0), len(committed_keyword_hits), offset + len(fallback_hits)),
                        'hasMore': offset + limit < len(committed_keyword_hits) or len(fallback_hits) > limit or bool(snapshot_keyword.get('hasMore')),
                        'retrievalMode':'keyword','coverage':{'message':'语义检索暂不可用，当前展示关键词结果，请检查本地模型'}}
            finally:
                self.foreground_queries -= 1

    @observed('search.clear', id_field='task_id')
    async def clear(self, account):
        account_dir = get_output_databases_dir() / account
        with account_work(account_dir):
            await self.pause_account(account)
            cfg = self.config(account)
            cfg.pop('active', None)
            if not cfg['enabled']: cfg['model']=None
            self.store.put('config', cfg, id=account, account=account)
            await account_to_thread(account_dir, self.index(account).clear)
            self.store.delete('fts_migration', account)
            await account_to_thread(account_dir, self.clear_message_plans, account)
            self.queries.clear()

    @observed('search.purge', id_field='task_id')
    def purge(self, account):
        self.revoked.add(account)
        self.fts_cancelled.add(account)
        active = False
        for job in self.store.list('index_job',account):
            self.cancelled.add(job['id'])
            active |= job['id'] in self.jobs and not self.jobs[job['id']].done()
        self.store.purge_account(account)
        self.index(account).clear()
        self.queries.clear()
        if not active:
            self.clear_message_plans(account)

    @observed('search.start', id_field='task_id')
    async def start(self):
        self.acceleration.stopping = False
        self.acceleration.start_detection(False)
        for cfg in self.store.list('config'):
            migration = self.store.get('fts_migration', cfg['account']) or {}
            self.start_fts_migration(cfg['account'], resume=bool(migration.get('resume_on_start')))
        for job in self.store.list('index_job'):
            if job['status'] in {'queued','running'} or job.get('resume_on_start'):
                try:
                    job.pop('resume_on_start',None)
                    self.update(job,status='paused')
                    await self.resume(job['account'], job['id'], resume_migration=False)
                except ValueError:
                    self.update(job, status='paused', error='配置已改变，请按当前范围重新建立索引')
        for job in self.store.list('download'):
            if job['status'] in {'queued','running'} or job.get('resume_on_start'): await self.downloads.start(job['id'])
        for component in (self.gpu, self.dml):
            gpu_job = self.store.get('gpu_component',component.job_id) or {}
            if gpu_job.get('status') in {'queued','running'} or gpu_job.get('resume_on_start'):
                from ..local_acceleration import start_component
                await start_component(self, component.backend, account=gpu_job.get('validation_account'))
        self.loop_task = asyncio.create_task(self.schedule())

    async def schedule(self):
        while True:
            await asyncio.sleep(60)
            try:
                if time.monotonic()-self.engine.last_used>300 and self.engine.lock.acquire(blocking=False):
                    try: self.engine.close()
                    finally: self.engine.lock.release()
                for cfg in self.store.list('config'):
                    self.start_fts_migration(cfg['account'])
                    if not cfg['enabled'] or not cfg['auto_update']: continue
                    # 已开启自动更新的旧配置也迁移为全账号，不必等用户首次提问。
                    # ensure_global 保留显式暂停、检查模型可用性并只发布已提交覆盖。
                    try:
                        await self.ensure_global(cfg['account'])
                    except ValueError as error: failures.report('search.schedule.'+cfg['account'], error)
                failures.recovered('search.scheduler')
            except Exception as error:
                failures.report('search.scheduler', error)

    @observed('search.stop', id_field='task_id')
    async def stop(self):
        if self.loop_task:
            self.loop_task.cancel()
            await asyncio.gather(self.loop_task, return_exceptions=True)
        migrating = [account for account, task in self.fts_jobs.items() if not task.done()]
        self.fts_cancelled.update(migrating)
        await asyncio.gather(*self.fts_jobs.values(), return_exceptions=True)
        for account in migrating:
            migration = self.store.get('fts_migration', account)
            if migration and migration['status'] == 'paused':
                self.store.put('fts_migration', {**migration, 'resume_on_start': True}, id=account, account=account)
        for id,task in self.jobs.items():
            if not task.done():
                self.cancelled.add(id)
                job=self.store.get('index_job',id)
                if job and job['status'] in {'running','queued'}:
                    self.restarting.add(id)
                    self.update(job,resume_on_start=True)
                    if job['status']=='queued':
                        task.cancel()
                        self.update(job,status='paused',stage='paused')
        await asyncio.gather(*self.jobs.values(), return_exceptions=True)
        downloading=[id for id,task in self.downloads.tasks.items() if not task.done()]
        await self.downloads.stop()
        for id in downloading:
            job=self.store.get('download',id)
            if job: self.downloads.update(job,resume_on_start=True)
        for component in (self.gpu, self.dml):
            gpu_running=component.task and not component.task.done()
            await component.pause()
            if gpu_running: component.update(resume_on_start=True)
        await self.acceleration.stop()
        self.engine.close()


_service = None


@contextmanager
def prioritize_foreground():
    """仅协调当前已运行的索引；嵌套查询、异常及取消都准确归还计数。"""
    service = _service
    if service is not None:
        service.foreground_queries += 1
    try:
        yield
    finally:
        if service is not None:
            service.foreground_queries -= 1


def get_local_search():
    global _service
    if _service is None: _service = LocalSearch()
    return _service
