"""只读工具适配器：所有数据均通过已有聊天服务获取。"""
from .diagnostics import observed, executor_call
from .agent_budget import MAX_READ_MESSAGES, size, message_payload
from .messages import message_identity
from ..account_workers import account_to_thread
from ..app_paths import get_output_databases_dir
import asyncio
import hashlib
import threading
from functools import wraps
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from collections import OrderedDict
from starlette.requests import Request


def foreground_read(function):
    """前台读取与结果整理期间让索引让步；不为单独工具调用创建后台服务。"""
    @wraps(function)
    async def wrapped(*args, **kwargs):
        from ..local_search.service import prioritize_foreground
        with prioritize_foreground():
            return await function(*args, **kwargs)
    return wrapped


def local_request():
    return Request({'type': 'http', 'method': 'GET', 'path': '/', 'headers': [],
                    'scheme': 'http', 'server': ('127.0.0.1', 10392), 'client': ('127.0.0.1', 0), 'query_string': b''})


LOCAL_ATTACHMENT_FIELDS = ('original_text', 'local_attachment_text', 'local_attachment_method', 'local_attachment_provenance')


def attachment_message_signature(message):
    """绑定本地提取结果的原消息版本；附件定位或原文变化后不能沿用旧识别文字。"""
    result = {key: message.get(key) for key in ('identity', 'username', 'anchor', 'time', 'kind')}
    result['text'] = message.get('original_text', message['text'])
    media = message.get('media') or {}
    result['media'] = {key: media.get(key) for key in
        ('fileMd5', 'imageMd5', 'imageFileId', 'videoMd5', 'videoFileId', 'fileSize')}
    return result


def retain_local_attachment(current, previous):
    """只给未变化的原消息补回结构化的本地派生证据。"""
    provenance = previous.get('local_attachment_provenance')
    if current.get('local_attachment_provenance') or not provenance:
        return current
    if provenance['message_signature'] != attachment_message_signature(current):
        return current
    from ..local_search.index import retrieval_text
    value = {**current, **{key: previous[key] for key in LOCAL_ATTACHMENT_FIELDS}}
    value['original_text'] = current['text']
    value['text'] = retrieval_text(value)
    return value


def normalize(account, username, raw, name=''):
    # 全局命中必须使用消息本身的会话身份，不能把空筛选条件写入来源。
    username = raw.get('username') or raw.get('conversationUsername') or username
    if not username:
        return None
    anchor = str(raw.get('id') or raw.get('anchorId') or '')
    if not anchor:
        return None
    timestamp = int(raw.get('createTime') or raw.get('create_time') or 0)
    identity = message_identity(raw.get('serverIdStr') or raw.get('serverId') or raw.get('server_id'),
                                anchor, timestamp, raw.get('type') or raw.get('local_type'))
    value = {'source': hashlib.sha256(f'{account}:{username}:{identity}'.encode()).hexdigest()[:24], 'identity': identity,
            'anchor': anchor, 'username': username, 'name': name or raw.get('conversationName') or username,
            'time': timestamp,
            'sender': raw.get('senderDisplayName') or raw.get('senderName') or raw.get('senderUsername') or '',
            'sender_id': raw.get('senderUsername') or '',
            'kind': raw.get('renderType', 'text'),
            'text': raw['aiText'] if isinstance(raw.get('aiText'), str) else
                    '\n'.join(str(raw.get(k) or '') for k in ('content', 'title', 'quoteTitle', 'quoteContent', 'voiceTranscript') if raw.get(k)),
            'match_methods': [m for m in raw.get('matchMethods', []) if m in ('keyword', 'semantic')],
            'media': raw}
    provenance = raw.get('localAttachmentProvenance')
    if provenance is not None:
        original, extracted = raw.get('originalAiText'), raw.get('localAttachmentText')
        if not isinstance(provenance, dict) or not isinstance(original, str) or not isinstance(extracted, str):
            raise ValueError('本地附件检索证据损坏')
        value.update(original_text=original, local_attachment_text=extracted,
                     local_attachment_method=provenance['extraction_method'], local_attachment_provenance=provenance)
        if provenance['account'] != account or provenance['message_signature'] != attachment_message_signature(value):
            raise ValueError('本地附件检索证据与原消息不一致')
        from ..local_search.index import retrieval_text
        if value['text'] != retrieval_text(value):
            raise ValueError('本地附件检索文字与结构化来源不一致')
    return value


def group_people_directory(account, usernames, people):
    """群名片只属于对应群；以完整联系人目录核对 ID，不从单个发言样本推断唯一性。"""
    from ..chat_helpers import _resolve_account_dir, _load_group_nickname_map_from_contact_db
    path = _resolve_account_dir(account) / 'contact.db'
    by_id = {p['username']: p for p in people if not p.get('conversation')}
    result = []
    for group in sorted(set(u for u in usernames if u.endswith('@chatroom'))):
        cards = _load_group_nickname_map_from_contact_db(path, group, list(by_id))
        for username, name in cards.items():
            person = by_id[username]
            result.append({**person, 'name': name, 'conversation': group,
                           'aliases': list(dict.fromkeys([name, person['name'], *person.get('aliases', [])]))})
    return result


class ChatTools:
    supports_time_prefetch = True

    def __init__(self):
        self._time_pages = OrderedDict()
        self._time_pages_lock = threading.Lock()

    def release_read_session(self, session):
        with self._time_pages_lock:
            for key in list(self._time_pages):
                if key[0].split(':')[0] == session.split(':')[0]:
                    self._time_pages.pop(key)

    async def group_people(self, account, usernames, people):
        return await account_to_thread(get_output_databases_dir() / account, group_people_directory, account, usernames, people)

    @foreground_read
    async def latest(self, account, usernames, start, end, *, sender=None, kind=None, offsets=None, limit=20, checkpoint=None):
        """读取全局最新一批；偏移只提交实际入选的筛选后消息。"""
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_READ_MESSAGES:
            raise ValueError(f'每批消息数量必须是 1 到 {MAX_READ_MESSAGES} 的整数')
        offsets = offsets or {}
        usernames = sorted(set(usernames))
        if any(not isinstance(offsets.get(u, 0), int) or isinstance(offsets.get(u, 0), bool) or
               offsets.get(u, 0) < 0 for u in usernames):
            raise ValueError('消息分页偏移必须是非负整数')
        from .messages import iter_message_pages

        def collect():
            candidates, warnings = [], []
            more = False
            next_offsets = {u: offsets.get(u, 0) for u in usernames}
            for username in usernames:
                if checkpoint:
                    checkpoint()
                stream = iter_message_pages(account, username, start, end, descending=True,
                    message_kind=kind, sender_id=sender, page_offset=next_offsets[username], page_size=limit + 1,
                    max_batch_chars=float('inf'), checkpoint=checkpoint)
                try:
                    page = next(stream)
                    more = more or page['has_more']
                    if page.get('warning'):
                        warnings.append(page['warning'])
                    for order, message in enumerate(page['messages']):
                        message['name'] = page.get('name') or username
                        candidates.append((-message['time'], username, order, message))
                finally:
                    stream.close()
            selected = sorted(candidates, key=lambda x: x[:3])[:limit]
            for _, username, _, _ in selected:
                next_offsets[username] += 1
            return {'messages': [x[3] for x in selected], 'has_more': more or len(candidates) > limit,
                'next_offsets': next_offsets, 'warning': '；'.join(dict.fromkeys(warnings))}
        return await account_to_thread(get_output_databases_dir() / account, collect)




    @foreground_read
    async def recent_set(self, account, usernames, start, end, count, checkpoint, sender=None, on_progress=None):
        """程序精确选择跨会话合计最近 N 条，堆内存最多保留 N 条。"""
        import heapq
        from .messages import iter_message_pages
        def collect():
            heap, warnings = [], []
            from .recent_bounds import recent_bounds
            bounds = recent_bounds(account, usernames, start, end, checkpoint)
            ordered = sorted(usernames, key=lambda u: -(bounds[u] if bounds[u] is not None else -1)) if bounds is not None else usernames
            pruned = 0
            for index, username in enumerate(ordered):
                checkpoint()
                # 已有 N 条后，更早消息不可能入选；边界秒仍完整保留参与比较。
                lower = max(start, heap[0][0]) if len(heap) == count else start
                if bounds is not None and (bounds[username] is None or bounds[username] < lower):
                    pruned += 1
                    if on_progress:
                        on_progress({'completed_conversations': index + 1, 'total_conversations': len(usernames),
                                     'selected': len(heap), 'boundary': heap[0][0] if heap else None})
                    continue
                def selection_key(row):
                    identity = message_identity(row.server_id, f'{row.db_stem}:{row.table_name}:{row.local_id}', row.create_time, row.local_type)
                    source = hashlib.sha256(f'{account}:{username}:{identity}'.encode()).hexdigest()[:24]
                    return row.create_time, source
                # 指定发言人时必须先筛人再取 N；不能在各群前 N 条上事后过滤。
                stream = iter_message_pages(account, username, lower, end - 1, count=None if sender else count,
                                             page_offset=0, checkpoint=checkpoint, count_key=selection_key)
                try:
                    for page in stream:
                        if page.get('warning'):
                            warnings.append(page['warning'])
                        for m in page['messages']:
                            m['name'] = page.get('name', username)
                            if sender and (m.get('sender_id') or m.get('media', {}).get('senderUsername') or m.get('sender')) != sender:
                                continue
                            entry = (m['time'], m['source'], m)
                            if len(heap) < count:
                                heapq.heappush(heap, entry)
                            elif entry[:2] > heap[0][:2]:
                                heapq.heapreplace(heap, entry)
                finally:
                    stream.close()
                if on_progress:
                    on_progress({'completed_conversations': index + 1, 'total_conversations': len(usernames),
                                 'selected': len(heap), 'boundary': heap[0][0] if heap else None})
            return {'messages': [x[2] for x in sorted(heap)], 'warning': '；'.join(dict.fromkeys(warnings)),
                    'selection': {'bounds_available': bounds is not None, 'pruned_conversations': pruned,
                                  'read_conversations': len(usernames) - pruned}}
        return await account_to_thread(get_output_databases_dir() / account, collect)

    async def time_window(self, account, username, start, end, capacity, state=None, checkpoint=None, *, session=None, probe_budget=None):
        from .agent_reading import read_window
        from .messages import iter_message_pages, filter_after
        key = (session, account, username, start, end)
        def page(lo, hi, budget, cursor):
            if checkpoint:
                checkpoint()
            # 缓存仅属于当前任务版本，查询范围和账号也进入键；其他任务和
            # 用户发起的新查询必须重新读取。数据库页与模型页分别控制大小。
            if session:
                with self._time_pages_lock:
                    cached = self._time_pages.get(key)
                    if cached:
                        self._time_pages.move_to_end(key)
                if cached and cached['lo'] <= lo and cached['hi'] == hi:
                    floor = cached['cursor'] or {'time': cached['lo'], 'ids': []}
                    current = cursor or {'time': lo, 'ids': []}
                    forward = current['time'] > floor['time'] or (
                        current['time'] == floor['time'] and set(floor['ids']) <= set(current['ids']))
                    if forward:
                        rows = cached['page']['messages']
                        rows = filter_after(rows, current)
                        if rows or not cached['page'].get('has_more'):
                            return {**cached['page'], 'messages': rows}
            fetch_budget = min(512 * 1024, max(128 * 1024, budget * 8)) if session else budget
            stream = iter_message_pages(account, username, lo, hi - 1, page_offset=0,
                page_size=MAX_READ_MESSAGES, cursor=cursor, emit_cursor=True,
                max_batch_bytes=fetch_budget, message_weight=lambda m: size(message_payload(m)), checkpoint=checkpoint)
            try:
                page = next(stream)
                # 已按正文预算、稳定游标取到的有序前缀，可直接消费，无需反复二分重查。
                page['budgeted_page'] = True
                for message in page.get('messages', []):
                    message.setdefault('name', page.get('name') or username)
                if session and not page.get('warning'):
                    weight = size(page)
                    # 连同本地媒体结构计算实际内存边界，不能只限制正文后
                    # 无限缓存附件。淘汰后依靠持久化身份游标继续，正确性不依赖缓存。
                    if weight <= 8 * 1024 * 1024:
                        with self._time_pages_lock:
                            self._time_pages[key] = {'lo': lo, 'hi': hi, 'cursor': cursor, 'page': page, 'weight': weight}
                            self._time_pages.move_to_end(key)
                            while len(self._time_pages) > 8 or sum(v['weight'] for v in self._time_pages.values()) > 8 * 1024 * 1024:
                                self._time_pages.popitem(last=False)
                return page
            finally:
                stream.close()
        async def read_page(lo, hi, budget, cursor):
            return await account_to_thread(get_output_databases_dir() / account, page, lo, hi, budget, cursor)
        return await read_window(read_page, start, end, capacity, state, probe_budget=probe_budget)

    @asynccontextmanager
    async def open_pages(self, account, username, start, end, offset, checkpoint, count=None, *, max_batch_bytes=None):
        """整次运行复用同一消息流；SQLite 游标在专用线程推进和关闭。"""
        from .messages import iter_message_pages
        closing = threading.Event()
        def check():
            if closing.is_set(): raise RuntimeError('读取已停止')
            checkpoint()
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='agent-reader')
        stream = iter_message_pages(account,username,start,end,count=count,page_offset=offset,
            page_size=MAX_READ_MESSAGES if max_batch_bytes is not None else 50,
            max_batch_chars=float('inf') if max_batch_bytes is not None else 12000,checkpoint=check,
            max_batch_bytes=max_batch_bytes, message_weight=lambda m: size(message_payload(m)))
        try:
            yield lambda: executor_call(executor,next,stream,None)
        finally:
            closing.set()
            try:
                await asyncio.shield(executor_call(executor,stream.close))
            finally:
                executor.shutdown(wait=False)

    @observed('agent.read.conversations')
    async def conversations(self, account):
        from ..chat_export_service import get_chat_export_targets_preview
        result = await account_to_thread(get_output_databases_dir() / account, get_chat_export_targets_preview,
            account=account, include_hidden=True, include_official=False)
        targets = {x['username']: x for x in result['targets']}
        return [{'username': x['username'], 'name': x.get('name') or x.get('displayName') or x['username'],
                 'isGroup': x.get('isGroup', x['username'].endswith('@chatroom'))} for x in targets.values()]

    async def people(self, account):
        return await account_to_thread(get_output_databases_dir() / account, self.people_directory, account)

    def people_directory(self, account):
        """人物目录独立于会话列表；备注和昵称均保留，仅以只读连接访问原联系人库。"""
        def load():
            import sqlite3
            from ..chat_helpers import _resolve_account_dir, _normalize_contact_text
            path = _resolve_account_dir(account) / 'contact.db'
            if not path.is_file():
                return []
            result = {}
            with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
                db.text_factory = bytes
                for table in ('contact', 'stranger'):
                    columns = {_normalize_contact_text(row[1]) for row in db.execute(f'PRAGMA table_info({table})')}
                    if 'username' not in columns:
                        continue
                    fields = [name for name in ('username', 'remark', 'nick_name', 'alias') if name in columns]
                    for row in db.execute(f"SELECT {','.join(fields)} FROM {table}"):
                        values = dict(zip(fields, map(_normalize_contact_text, row)))
                        username = values['username']
                        if not username or username.endswith('@chatroom') or username.startswith('gh_') or username in result:
                            continue
                        aliases = list(dict.fromkeys(values[k] for k in ('remark', 'nick_name', 'alias') if values.get(k)))
                        result[username] = {'username': username, 'name': aliases[0] if aliases else username,
                                            'aliases': aliases, 'isGroup': False}
            return list(result.values())
        return load()

    @observed('agent.read.search')
    async def search(self, account, username, query, start, end, offset, sender=None, *,
                     retrieval_mode='keyword', search_ticket=None, strict_paging=True):
        from ..routers.chat import search_chat_messages
        result = await search_chat_messages(local_request(), q=query, account=account, username=username or None, sender=sender,
                                           start_time=start, end_time=max(start, end - 1), offset=offset, limit=50, source='auto', include_hidden=True,
                                           retrieval_mode=retrieval_mode, search_ticket=search_ticket,
                                           strict_paging=strict_paging)
        messages = [normalize(account, username, x) for x in result.get('hits', [])]
        device_note = result.get('device',{}).get('reason','')
        return {'messages': [x for x in messages if x], 'has_more': result.get('hasMore', False),
                'next_offset': offset + len(result.get('hits', [])) if result.get('hasMore') else None,
                'requested_retrieval_mode': retrieval_mode, 'retrieval_mode': result['retrievalMode'],
                'search_ticket': result.get('searchTicket'), 'device':result.get('device'),
                'match_counts': {method: sum(method in x.get('match_methods', []) for x in messages if x)
                                 for method in ('keyword', 'semantic')},
                'warning': '；'.join(filter(None,[result.get('coverage', {}).get('message') or '搜索索引来自本地快照；尚未解析的图片内容不在文字搜索范围内。',device_note])),
                'data_source': 'snapshot_index', 'freshness': {'kind': 'snapshot'}, 'start': start, 'end': end}

    @observed('agent.read.read')
    async def read(self, account, username, start, end, offset, count=None, *, max_batch_bytes=None):
        # 直接复用范围读取器，固定截止时间。分页按稳定来源排序，避免同秒消息遗漏。
        from .messages import read_messages
        result = await account_to_thread(get_output_databases_dir() / account, read_messages, account, username, start, end, count, page_offset=offset,
            page_size=MAX_READ_MESSAGES if max_batch_bytes is not None else 50, max_batch_bytes=max_batch_bytes,
            message_weight=lambda m: size(message_payload(m)))
        # 数据渠道与单条消息编号分开命名，避免模型把 realtime 当作消息引用。
        result['data_source'] = result.pop('source', 'auto')
        values = result['messages']
        for item in values:
            item['name'] = result['name']
        return {**result, 'messages': values, 'start': start, 'end': end}

    @observed('agent.read.context')
    async def context(self, account, evidence):
        from ..routers.chat import get_chat_messages_around
        result = await get_chat_messages_around(local_request(), username=evidence['username'], anchor_id=evidence['anchor'],
                                               account=account, before=10, after=10, source='auto')
        values = [normalize(account, evidence['username'], x, evidence.get('name', '')) for x in result.get('messages', [])]
        values = [retain_local_attachment(value, evidence)
                  if value and value['username'] == evidence['username'] and value['anchor'] == evidence['anchor']
                  else value for value in values]
        return {'messages': [x for x in values if x], 'warning': result.get('warning', ''), 'data_source': result.get('source', 'auto')}
