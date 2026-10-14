"""DeepAgents 微信工具。范围、分页和已分析覆盖由程序保存。"""
import asyncio
import calendar
import hashlib
import json
import re
import time
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

from langchain_core.tools import tool
from pydantic import BaseModel, Field, StrictInt, StrictStr
from typing import Annotated, Literal

from .agent_budget import message_payload, size
from .agent_context import explicit_clock_range
from .agent_global import resolve_directory_name, comparable_name
from .agent_references import material_references, reference_id
from .deep_synchronization import serialized
from .deep_validation import requires_complete_analysis, requires_findings
from .deep_calculation import CalculationTerm, calculate
from .deep_planning import REVISION, MainWork, BranchWork
from .deep_partition import capacity


class ScopeError(ValueError):
    """范围错误保留稳定编号，供模型恢复与界面展示。"""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


ScopeTime = Annotated[StrictStr | StrictInt, Field(description='ISO 日期时间或 Unix 秒整数（也可用整数字符串），不是毫秒；省略时使用默认边界')]
MEDIA_PROGRESS = ContextVar('deep_media_progress', default=None)


class FindingInput(BaseModel):
    """把发现的约束暴露给模型，避免只有 list[dict] 时猜测字段。"""
    text: str = Field(min_length=1, description='由所列本页来源支持的事实正文')
    sources: list[str] = Field(min_length=1, description='本页 messages 中的真实 source 编号，不带引用括号')
    quote: str | None = Field(default=None, description='可省略的逐字引文；概括放在 text 中。与对应原文不一致的附加引文会被舍弃')
    entities: list[str] | None = Field(default=None, description='本页原文明确提到的实体；不能仅凭同名认定同一人')
    event_time: str | None = Field(default=None, description='原文明示的事件时间，未知时省略；不得用消息发送时刻代替')
    relation: str | None = Field(default=None, description='需要与其他发现关联的线索，不把推测当成事实')
    evidence_status: Literal['supported', 'uncertain', 'conflicting'] | None = Field(default=None, description='省略表示原文支持；不确定或冲突必须标记')


class ChatGateway:
    def __init__(self, service, run_id, version):
        self.service, self.id, self.version = service, run_id, version
        self.lock = asyncio.Lock()
        self.topic = None
        if service.run(run_id).get('library_scope') is not None:
            from .topic_materials import TopicMaterials
            self.topic = TopicMaterials(service, run_id, version)

    def guard(self):
        run = self.service.guard(self.id)
        if run['version'] != self.version:
            from .agent_service import Revised
            raise Revised()
        return run

    def get(self, key):
        self.guard()
        return self.service.workspace.get(self.id, self.version, key)

    @serialized
    def put(self, key, kind, value):
        self.guard()
        self.service.workspace.put(self.id, self.version, key, kind, value)

    def scope(self, handle):
        value = self.get('scope:' + handle) if isinstance(handle, str) else None
        if not value:
            raise ScopeError('invalid_scope_handle', '范围句柄不存在或不属于当前任务版本，请重新选择本轮范围')
        return value

    def previous_filters(self):
        """补充版本只继承自己的条件快照，不能退回更早一轮。"""
        run = self.guard()
        if run['version'] > 1:
            snapshot = self.service.workspace.get(self.id, self.version - 1, 'query:next_filters')
            if (snapshot and snapshot.get('origin_run') == self.id
                    and snapshot.get('origin_version') == self.version - 1):
                return snapshot.get('filters') or {}
            return {}
        prior = self.service.store.get('agent_run', run.get('previous_run_id') or '')
        if not prior:
            return {}
        if prior['account'] != run['account'] or prior['thread_id'] != run['thread_id']:
            raise ValueError('不能继承其他账号或 AI 对话条件')
        return prior.get('query_filters') or {}

    def progress_state(self):
        """给模型可直接执行的状态；统计覆盖不表示已分析。"""
        if self.topic is not None:
            return [self.topic.coverage()]
        states = self.scopes()
        states = [{**s, 'read_complete': self.scope_covered(s, states,
            analyzed=s['complete_required'] and s['mode'] != 'statistics', ignore_warnings=True)} for s in states]
        return [{'scope_handle': s['handle'], 'names': s.get('names', {}), 'mode': s['mode'],
            'time_range': {k: s[k] for k in ('start', 'end')}, 'read_order': s.get('read_order', 'asc'),
            'read_complete': s['read_complete'], 'complete_required': s['complete_required'],
            'statistics_complete': s['mode'] == 'statistics' and s['read_complete'] and not s.get('warnings'),
            'analysis_complete': s['mode'] != 'statistics' and s['complete_required'] and s['read_complete'] and not s['pending_page'] and not s.get('warnings'),
            'pending_page': s['pending_page'] or None, 'pages': s['pages'],
            **({'execution_mode': self.get(s['analysis_plan'])['mode'], 'plan_handle': s['analysis_plan']}
                if s.get('analysis_plan') and self.get(s['analysis_plan']) else {}),
            'committed_pages': s['committed_pages'],
            'next_args': {'scope_handle': s['handle'], **({'page_id': s['pending_page']} if s['pending_page'] else {})},
            'next_tool': 'commit_findings' if s['pending_page'] else
                'count_messages' if s['mode'] == 'statistics' and not s['read_complete'] else
                'task' if s.get('analysis_plan') and self.get(s['analysis_plan'])['mode'] == 'parallel' and not s['read_complete'] else
                'read_messages' if s['mode'] != 'statistics' and not s['read_complete'] else None} for s in states]

    @staticmethod
    def permits(state, message):
        return (message['username'] in state['conversations'] and state['start'] <= message['time'] < state['end'] and
            (not state.get('sender') or state['sender'] == (message.get('sender_id') or message.get('media', {}).get('senderUsername') or message.get('sender'))))

    def interval(self, phrase, start, end):
        run = self.guard()
        zone = timezone(timedelta(seconds=run['timezone_offset']))
        now = datetime.fromtimestamp(run['cutoff'], zone)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if phrase:
            if phrase.strip() in ('全历史', '全部历史', '所有历史', '全部时间', '不限时间', '全时间段', '整个历史', 'all', 'all_time'):
                phrase = ''
            explicit = explicit_clock_range(phrase, run['timezone_offset'])
            if explicit:
                return explicit
            if phrase in ('今天', '昨天', '前天'):
                days = ('今天', '昨天', '前天').index(phrase)
                lo = midnight - timedelta(days=days)
                return {'start': int(lo.timestamp()), 'end': min(run['cutoff'], int((lo + timedelta(days=1)).timestamp()))}
            if phrase in ('本周', '这周', '上周'):
                lo = midnight - timedelta(days=midnight.weekday() + (7 if phrase == '上周' else 0))
                return {'start': int(lo.timestamp()), 'end': min(run['cutoff'], int((lo + timedelta(days=7)).timestamp()))}
            # 常见相对时间由程序统一解释，模型无需先失败一次再补写 ISO 边界。
            relative = re.fullmatch(r'(?:最近|近|过去)\s*(\d+|一|二|两|三|四|五|六|七|八|九|十)\s*(天|日|周|星期|个月|月|年)(?:内)?', phrase.strip())
            if relative:
                raw, unit = relative.groups()
                number = int(raw) if raw.isdigit() else {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
                    '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}[raw]
                if unit in ('天', '日'):
                    lo = now - timedelta(days=number)
                elif unit in ('周', '星期'):
                    lo = now - timedelta(days=number * 7)
                else:
                    months = number * (12 if unit == '年' else 1)
                    month_index = now.year * 12 + now.month - 1 - months
                    year, month = divmod(month_index, 12)
                    month += 1
                    lo = now.replace(year=year, month=month, day=min(now.day, calendar.monthrange(year, month)[1]))
                return {'start': int(lo.timestamp()), 'end': run['cutoff']}
        def stamp(value, default):
            if value == '' or value is None:
                return default
            error = '时间格式无效：请使用 ISO 日期时间或 Unix 秒整数，不支持布尔值、小数或毫秒时间戳'
            if type(value) not in (str, int):
                raise ValueError(error)
            value = value.strip() if isinstance(value, str) else value
            if type(value) is int or re.fullmatch(r'[+-]?\d+', value):
                seconds = int(value)
                # 秒输入限定到四位年份可表达的范围，防止当代毫秒值被截成截止时间。
                if not 0 <= seconds <= 253402300799:
                    raise ValueError(error)
                return seconds
            try:
                parsed = datetime.fromisoformat(value)
                return int((parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)).timestamp())
            except (ValueError, OverflowError, OSError):
                raise ValueError(error) from None
        lo, hi = stamp(start, 0), min(stamp(end, run['cutoff']), run['cutoff'])
        if not 0 <= lo <= hi:
            raise ValueError('时间区间无效，结束时刻不包含在读取范围内')
        if phrase and not start and not end:
            raise ValueError('请把日期条件明确为本地 ISO 日期时间，或向用户澄清')
        return {'start': lo, 'end': hi}

    async def select(self, conversations=None, all_chats=False, time_phrase='', start='', end='',
                     sender=None, exclude=None, complete=False, mode='search', reuse_previous=False, message_count=None, clear_filters=None):
        async with self.lock:
            run = self.guard()
            if mode not in ('search', 'statistics'):
                raise ValueError('mode 只能为 search 或 statistics')
            if run.get('subtask_plan_version') == REVISION and not run.get('parent_run_id'):
                # 模型自行填写 complete 不能把普通问题升级为全历史核查。
                complete = requires_complete_analysis(run.get('input_digest', ''))
            else:
                complete = complete or (not run.get('parent_run_id') and requires_complete_analysis(run.get('input_digest', '')))
            thread = self.service.thread(run['thread_id'], run['account'])
            standalone = run.get('origin') == 'agent'
            contacts = None if standalone else self.get('directory:conversations')
            if contacts is None:
                contacts = await self.service.agent_directory(run['account'], run['chat_scope']) if standalone else await self.service.tools.conversations(run['account'])
                self.guard()
                if standalone:
                    full_directory = contacts
                    if run['chat_scope'] is not None:
                        contacts = [c for c in contacts if c['username'] in run['chat_scope']]
                    standalone_allowed = {c['username'] for c in contacts}
                if run.get('bound_scope'):
                    contacts = [c for c in contacts if c['username'] in run['bound_scope']['conversations']]
                # 同一输入版本复用名称目录；补充要求生成新版本后重新获取。
                self.put('directory:conversations', 'deep_directory', contacts)
            self.guard()
            allowed = {c['username']: c for c in contacts}
            # 在真正查询时才匹配用户点名的对象；模型失败后不能改用全账号范围。
            # 只采用足够长的明确名称，避免“我”“妈妈”等短词误识别为本轮对象。
            question_name = comparable_name(run.get('input_digest', ''))
            named = [c for c in (full_directory if standalone else contacts) if (len(comparable_name(c['name'])) >= 4 and comparable_name(c['name']) in question_name)
                or c['username'] in run.get('input_digest', '')]
            named_ids = {c['username'] for c in named}
            exclusion_clauses = [comparable_name(m[1]) for m in re.finditer(
                r'(?:排除|除了|不含|不查)([^，。；\n]*)', run.get('input_digest', ''))]
            explicit_excluded = {c['username'] for c in named if any(
                comparable_name(c['name']) in clause or c['username'] in clause for clause in exclusion_clauses)}
            positive_ids = named_ids - explicit_excluded
            if standalone:
                permitted_names = {comparable_name(c['name']) for c in full_directory if c['username'] in standalone_allowed}
                if any(c['username'] in positive_ids and c['username'] not in standalone_allowed
                    and (c['username'] in run.get('input_digest', '') or comparable_name(c['name']) not in permitted_names) for c in named):
                    raise ScopeError('chat_scope_violation', '请求的会话超出此独立 Agent 对话的固定范围，请新建对话')
                named = [c for c in named if c['username'] in allowed]
                named_ids &= allowed.keys()
                positive_ids &= allowed.keys()
            inherited = run.get('restart_filters') or {}
            if reuse_previous:
                inherited = self.previous_filters()
            inherited = dict(inherited)
            for key in clear_filters or []:
                if key not in ('sender', 'message_count', 'time_range', 'conversations'):
                    raise ValueError('只能清除已声明的查询条件')
                inherited.pop(key, None)
            if inherited and conversations is None and not all_chats:
                conversations = inherited.get('conversations')
            requested = conversations or ([] if all_chats else [thread['username']] if thread.get('username') else [])
            selected = []
            for name in requested:
                matches = resolve_directory_name(contacts, name)
                if len(matches) != 1:
                    if standalone and not matches:
                        raise ScopeError('chat_scope_violation', '请求的会话不在此独立 Agent 对话的可读范围内')
                    return {'clarification': '会话名称不唯一或不存在', 'candidates': matches[:8]}
                selected.append(matches[0]['username'])
            if not requested:
                selected = list(allowed)
            excluded = set(explicit_excluded)
            for name in exclude or []:
                matches = resolve_directory_name(contacts, name)
                if len(matches) != 1:
                    return {'clarification': '待排除的会话名称不唯一', 'candidates': matches[:8]}
                excluded.add(matches[0]['username'])
            selected = [u for u in dict.fromkeys(selected) if u not in excluded]
            if not run.get('bound_scope') and requires_findings(run.get('input_digest', '')) and re.search(r'(?:全部|所有|全账号)(?:的)?(?:聊天|会话|群)', run.get('input_digest', '')):
                self.service.update(self.id, required_conversations=[u for u in allowed if u not in excluded])
            if named_ids and not run.get('bound_scope'):
                # 排除句只能影响其中的对象，不能让同一句中的正向点名约束失效。
                explicitly_all = bool(re.search(r'(?:全部|所有|全账号)(?:的)?(?:聊天|会话|群)', run.get('input_digest', '')))
                if positive_ids and not explicitly_all and not set(selected) <= positive_ids:
                    raise ValueError('本轮用户明确指定了会话，不能扩大到其他会话。请通过 conversations 选择点名对象：' + '、'.join(c['name'] for c in named[:8]))
                if positive_ids and not explicitly_all:
                    self.service.update(self.id, required_conversations=sorted(positive_ids))
            if run.get('bound_scope'):
                bound = run['bound_scope']
                if not set(selected) <= set(bound['conversations']):
                    raise ValueError('子任务不能扩大分配范围')
            if not selected:
                return {'clarification': '没有符合条件的可读会话'}
            sender = (inherited.get('sender') or '') if sender is None else sender
            if message_count is None and reuse_previous and 'message_count' not in (clear_filters or []):
                message_count = inherited.get('message_count')
            question = run.get('input_digest', '')
            explicit = explicit_clock_range(question, run['timezone_offset'])
            if explicit is None:
                date = r'\d{4}(?:年|-|/)\d{1,2}(?:月|-|/)\d{1,2}日?[\sT]*\d{1,2}[:：]\d{2}(?:[:：]\d{2})?'
                ranges = list(re.finditer(date + r'\s*(?:到|至|~|～|—)\s*' + date, question))
                if len(ranges) == 1:
                    explicit = explicit_clock_range(ranges[0][0], run['timezone_offset'])
            if explicit and not run.get('bound_scope'):
                # 用户明确写出的边界比模型换算的时间值优先，避免少读或多读一分钟。
                interval = {**explicit, 'end': min(explicit['end'], run['cutoff'])}
            elif run.get('bound_scope') and not time_phrase and start == '' and end == '':
                interval = {k: run['bound_scope'][k] for k in ('start', 'end')}
            elif inherited and not time_phrase and start == '' and end == '':
                interval = inherited.get('time_range') or self.interval('', '', '')
            else:
                interval = self.interval(time_phrase, start, end)
            if not 0 <= interval['start'] <= interval['end'] <= run['cutoff']:
                raise ValueError('时间区间无效，结束时刻不包含在读取范围内')
            if run.get('bound_scope'):
                bound = run['bound_scope']
                if interval['start'] < bound['start'] or interval['end'] > bound['end']:
                    raise ValueError('子任务不能扩大分配的时间区间')
                if bound.get('sender'):
                    if sender and sender != bound['sender']:
                        raise ValueError('子任务不能修改分配的发言人')
                    sender = bound['sender']
                if run.get('child_role') == 'range-analyst' and (set(selected) != set(bound['conversations'])
                    or any(interval[k] != bound[k] for k in ('start', 'end'))):
                    raise ValueError('范围分析员必须处理完整的分配范围，不能缩小范围宣称完成')
                complete = complete or run.get('child_role') == 'range-analyst'
            if sender:
                people = await self.service.tools.people(run['account'])
                matches = resolve_directory_name(people, sender)
                if len(matches) != 1:
                    return {'clarification': '发言人名称不唯一', 'candidates': matches[:8]}
                sender = matches[0]['username']
            if message_count is not None and (type(message_count) is not int or message_count <= 0):
                raise ValueError('最近消息条数必须为正整数')
            if message_count is not None:
                # “最近的100条”和“最近100条”是同一数量要求，不能因语气助词拒绝合法范围。
                requested_count = re.search(r'(?:最近|最后|最新|近)\s*(?:的\s*)?(\d+)\s*条', run.get('input_digest', ''))
                inherited_count = inherited.get('message_count') if reuse_previous else None
                if not ((requested_count and int(requested_count[1]) == message_count) or inherited_count == message_count):
                    if requested_count:
                        raise ValueError(f'用户明确要求最近 {int(requested_count[1])} 条消息，请将 message_count 设为 {int(requested_count[1])}，不要省略或改变条数。')
                    raise ValueError('message_count 是用户明确要求的最近消息总数，不是分页大小。当前要求未指定该条数，请省略此参数以读取全部范围。')
            spec = {'conversations': selected, **interval, 'sender': sender, 'complete_required': complete or mode == 'statistics', 'mode': mode,
                'message_count': message_count,
                'read_order': 'asc' if complete or mode == 'statistics' or message_count else 'desc'}
            handle = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:24]
            state = self.get('scope:' + handle) or {**spec, 'handle': handle, 'conversation_index': 0, 'cursor': '',
                'read_complete': False, 'pending_page': '', 'pages': 0, 'committed_pages': 0}
            state['names'] = {u: allowed[u]['name'] for u in selected}
            self.put('scope:' + handle, 'deep_scope', state)
            combined = list(dict.fromkeys(u for s in self.scopes() for u in s['conversations']))
            thread.update(scope=list(allowed), account_wide=True)
            self.service.store.put('agent_thread', thread)
            self.service.update(self.id, query_scope=combined, query_filters={'conversations': combined, 'time_range': interval, 'sender': sender, 'message_count': message_count},
                time_range=interval, coverage_state='partial', scope_handle=handle,
                intent={'mode': mode, 'objective': run.get('input_digest', ''), 'message_count': message_count}, applied_version=self.version)
            self.service.deep_index(self.id)
            if spec['complete_required']:
                from .deep_guide import ANALYSIS_GUIDE
                from .deep_backend import TaskBackend
                self.put('file:/skills/wechat-analysis.md', 'deep_file', TaskBackend.file_data(ANALYSIS_GUIDE))
            result = {'scope_handle': handle, 'conversation_count': len(selected), 'names': [allowed[u]['name'] for u in selected[:8]],
                'time_range': interval, 'complete_required': spec['complete_required'], 'read_order': spec['read_order'],
                **({'file_lookup_tool': 'list_files', 'instruction': '普通读取从最新消息开始。查找文件先 list_files，再 analyze_media 读取目标文件；不必翻完聊天历史。'}
                   if not spec['complete_required'] and not message_count else {}),
                **({'analysis_guide': '/skills/wechat-analysis.md'} if spec['complete_required'] else {})}
            if (run.get('subtask_plan_version') == 1 and not run.get('parent_run_id') and complete
                    and mode != 'statistics' and not state.get('pending_page') and not state.get('read_complete')):
                plan = await self.service.analysis_plans.prepare(self, state)
                result.update(plan_handle=plan['id'], execution_mode=plan['mode'],
                    next_tool='task' if plan['mode'] == 'parallel' else 'read_messages',
                    instruction='大范围已按内容量准备分片，请调用 task，subagent_type=range-analyst；程序自动并行并复用预读。'
                        if plan['mode'] == 'parallel' else '范围较小，直接 read_messages 使用已缓存原文，无需子任务。')
            return result

    @serialized
    def save_messages(self, messages, originals=None):
        run = self.guard()
        values = originals if originals is not None else messages
        aliases = run['evidence'].put_many(values)
        values = [{**m, 'source': aliases.get(m['source'], m['source'])} for m in values]
        refs = material_references(run['account'], values, existing=run.get('references', {}))
        self.service.update(self.id, read_count=len(run['evidence']), references=refs)
        # 搜索和回查也改变资料集合，覆盖快照必须与总读取数同步。
        if run.get('scope_handle'):
            self.coverage()
        payload = []
        for m in messages:
            key = aliases.get(m['source'], m['source'])
            item = {**message_payload({**m, 'source': key}, run['timezone_offset']), 'path': '/materials/' + key + '.json'}
            item.update({field: m[field] for field in ('text_offset', 'next_text_offset', 'total_length') if field in m})
            person = reference_id('person', run['account'], m.get('sender_id') or m.get('media', {}).get('senderUsername'))
            if person in refs:
                item['sender_reference'] = f'[[person:{person}]]'
            picture = reference_id('image', run['account'], f"{m['username']}:{m.get('anchor', key)}")
            if picture in refs:
                item['image_reference'] = f'[[image:{picture}]]'
            payload.append(item)
        return payload

    async def fetch_page(self, state, capacity, *, probe_budget=None):
        """只推进读取位置；覆盖提交仍由主任务或分片拥有者负责。"""
        run = self.guard()
        username = state['conversations'][state['conversation_index']]
        if state.get('message_count'):
            result = await self.recent_page(state, capacity, probe_budget=probe_budget)
            username = None
        elif state.get('read_order') == 'desc':
            result = await self.latest_page(state, capacity, probe_budget=probe_budget)
            username = None
        elif hasattr(self.service.tools, 'time_window'):
            options = {'probe_budget': probe_budget} if probe_budget is not None else {}
            result = await self.service.read_time(self.id, username, state['start'], state['end'], capacity, state['cursor'], **options)
        else:
            result = await self.service.tools.read(run['account'], username, state['start'], state['end'], int(state['cursor'] or 0), max_batch_bytes=capacity)
        self.guard()
        permitted = lambda m: self.permits(state, m) and (username is None or m['username'] == username)
        result = {**result, 'messages': [m for m in result.get('messages', []) if permitted(m)],
            'originals': [m for m in result.get('originals', result.get('messages', [])) if permitted(m)]}
        following = {**state, 'warnings': list(dict.fromkeys([*state.get('warnings', []), *([result['warning']] if result.get('warning') else [])]))}
        if result.get('has_more'):
            cursor = result.get('next_cursor') or result.get('next_offset')
            if cursor is None or str(cursor) == str(state['cursor']):
                raise ValueError('读取游标未推进，已保留当前页面')
            following['cursor'] = str(cursor)
        else:
            following.update(conversation_index=state['conversation_index'] + 1, cursor='')
            following['read_complete'] = username is None or following['conversation_index'] >= len(state['conversations'])
        return result, following

    async def read_next(self, handle):
        async with self.lock:
            state = self.scope(handle)
            if state.get('pending_page'):
                pending = self.get(state['pending_page'])
                if pending and isinstance(pending, dict) and 'result' in pending:
                    return pending['result']
                return self.get(state['pending_page'])['result']
            if state['read_complete']:
                return {'complete': True, 'requires_commit': False, 'messages': [], 'scope_handle': handle,
                    **({'coverage': 'search_only'} if self.guard().get('work_query') else {}),
                    'instruction': '范围已经处理完成，没有待提交页面；直接使用已保存发现回答，不再读取或提交。'}
            run = self.guard()
            if run.get('subtask_plan_version') == REVISION and run.get('work_query'):
                return await self.service.planned_work.read_query(self, state)
            if run.get('manifest_id'):
                return await self.service.read_manifest(self, state)
            if state.get('analysis_plan'):
                plan = self.get(state['analysis_plan'])
                if plan and plan['mode'] == 'parallel':
                    raise ScopeError('parallel_required', '范围已准备并行分析，请调用 task，subagent_type=range-analyst，scope_handle=' + handle)
            username = state['conversations'][state['conversation_index']]
            batch_capacity = capacity(self.service, run)
            cached = self.get(f"prefetch:{handle}:{state.get('prefetch_index', 0)}")
            if cached:
                result, following = cached['result'], cached['next']
                following = {**following, 'prefetch_index': state.get('prefetch_index', 0) + 1}
            else:
                result, following = await self.fetch_page(state, batch_capacity)
            if state.get('message_count') or state.get('read_order') == 'desc':
                username = None
            self.guard()
            def permitted(m):
                return m['username'] in state['conversations'] and (username is None or m['username'] == username) and state['start'] <= m['time'] < state['end'] and (not state['sender'] or
                    state['sender'] == (m.get('sender_id') or m.get('media', {}).get('senderUsername') or m.get('sender')))
            messages = [m for m in result.get('messages', []) if permitted(m)]
            originals = [m for m in result.get('originals', messages) if permitted(m)]
            payload = self.save_messages(messages, originals)
            next_state = {**state, **{k: following[k] for k in ('cursor', 'conversation_index', 'read_complete', 'prefetch_index') if k in following}, 'pages': state['pages'] + 1,
                'warnings': list(dict.fromkeys([*state.get('warnings', []), *([result['warning']] if result.get('warning') else [])]))}
            page_id = f'page:{handle}:{state["pages"]:08d}'
            out = {'page_id': page_id, 'scope_handle': handle, 'cursor': next_state.get('cursor', ''), 'messages': payload, 'has_more': not next_state['read_complete'],
                'requires_commit': state['complete_required'] and state['mode'] != 'statistics', 'warning': result.get('warning', '')}
            if out['requires_commit']:
                next_state['pending_page'] = page_id
            else:
                next_state['committed_pages'] += 1
            self.service.workspace.put_pieces(self.id, self.version, [(page_id, 'deep_page', {'result': out, 'cursor': next_state.get('cursor', ''),
                'covered': [{'source': m['source'], 'start': m.get('text_offset', 0),
                    'end': m.get('text_offset', 0) + len(m.get('text', ''))} for m in payload]}),
                ('scope:' + handle, 'deep_scope', next_state)])
            self.coverage()
            return out

    @serialized
    def commit(self, handle, page_id, findings):
        state = self.scope(handle)
        page = self.get(page_id)
        if not page or page['result']['scope_handle'] != handle:
            raise ValueError('页面不属于当前范围')
        if page.get('committed'):
            return {'saved': True, 'reused': True}
        if state['pending_page'] != page_id:
            if not page['result'].get('requires_commit'):
                return {'saved': False, 'requires_commit': False, 'scope_handle': handle,
                    'instruction': '该页无需提交，不计为已分析。普通问答可直接引用原文；完整分析请先选择 complete=true 的范围。'}
            raise ValueError('只能提交当前待分析页面')
        allowed = {c['source'] for c in page['covered']}
        pieces = []
        errors = []
        saved_findings, dropped_quotes = [], []
        for index, finding in enumerate(findings):
            if not isinstance(finding, dict) or not finding.get('text') or not finding.get('sources'):
                errors.append(f'第 {index + 1} 项缺少事实正文或来源列表')
                continue
            invalid = set(finding['sources']) - allowed
            if invalid:
                errors.append(f'第 {index + 1} 项来源不属于本页：' + ', '.join(sorted(invalid)[:8]))
                continue
            quote = finding.get('quote')
            if quote and not any(quote in m.get('text', '') for m in page['result']['messages'] if m['source'] in finding['sources']):
                # 引文是可选附加信息；丢弃不精确的引文，不让有效来源的一整批发现重做。
                finding = {key: value for key, value in finding.items() if key != 'quote'}
                dropped_quotes.append(index + 1)
            finding = {k: v for k, v in finding.items() if k in ('text', 'sources', 'quote', 'entities', 'event_time', 'relation', 'evidence_status')}
            saved_findings.append(finding)
            pieces.append((f'finding:{page_id}:{index:05d}', 'finding', finding))
        if errors:
            # 一次指出错误位置，避免整批盲重试或试交一条就把整页标成已分析。
            raise ValueError('；'.join(errors[:12]) + '。本次未保存任何发现。请按本页原文修正上述项，'
                '跨页事实留在对应页，不要猜来源；重新一次性提交本页全部发现，不要用单条试提交代替完整分析。')
        batch_index = state['committed_pages'] + 1
        # 批次编号属于范围；文件身份同时包含任务、版本与页面，避免范围及继承历史互相覆盖。
        note_identity = hashlib.sha256(json.dumps([self.id, self.version, page_id]).encode('utf-8')).hexdigest()
        note_path = f'/notes/batch_{batch_index:05d}_{note_identity}.json'
        page.update(committed=True, batch_index=batch_index, note_path=note_path)
        state.update(pending_page='', committed_pages=batch_index)

        # Build structured batch stage note
        messages = page.get('result', {}).get('messages', [])
        sources = [m['source'] for m in messages if isinstance(m, dict) and 'source' in m]
        run = self.guard()
        tz_offset = run.get('timezone_offset', 0) if isinstance(run, dict) else 0
        message_mappings = {}
        for m in messages:
            if not isinstance(m, dict) or 'source' not in m:
                continue
            sent_at = m.get('sent_at')
            if not sent_at and m.get('time'):
                try:
                    sent_at = datetime.fromtimestamp(m['time'], timezone(timedelta(seconds=tz_offset))).isoformat(timespec='seconds')
                except (KeyError, TypeError, ValueError, OverflowError, OSError):
                    sent_at = None
            message_mappings[m['source']] = {
                'sender': m.get('sender'),
                'sent_at': sent_at,
                'time': m.get('time'),
                'text': m.get('text', ''),
            }
        cursor_val = str(page.get('cursor') or (page.get('result') or {}).get('cursor') or state.get('cursor', ''))
        note_dict = {
            'batch_index': batch_index,
            'scope_handle': handle,
            'cursor': cursor_val,
            'messages_count': len(sources),
            'sources': sources,
            'message_mappings': message_mappings,
            'facts': saved_findings,
            'committed_at': datetime.now(timezone.utc).isoformat(),
        }
        note_json = json.dumps(note_dict, ensure_ascii=False, indent=2)

        pieces.extend([
            (page_id, 'deep_page', page),
            ('scope:' + handle, 'deep_scope', state),
            ('note:' + page_id, 'stage_note', {
                'covered': page['covered'],
                'items': saved_findings,
                'batch_index': batch_index,
                'note_path': note_path,
            }),
        ])
        from .deep_backend import TaskBackend
        backend = TaskBackend(self.service, self.id, self.version)
        write_res = backend.write(note_path, note_json, pieces=pieces)
        if write_res.error:
            raise ValueError(f'保存阶段笔记失败: {write_res.error}')
        self.coverage()
        result = {
            'saved': True,
            'findings': len(saved_findings),
            'has_more': not state['read_complete'],
            'batch_index': batch_index,
            'note_path': note_path,
        }
        if dropped_quotes:
            result.update(dropped_quote_count=len(dropped_quotes),
                note=f'已移除 {len(dropped_quotes)} 处未与原文逐字一致的附加引文；分析结果及来源已保存。后续请以对应消息原文为准。')
        return result

    def scopes(self):
        self.guard()
        with self.service.store.connection() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT body FROM agent_piece WHERE run_id=? AND version=? AND kind='deep_scope'", (self.id, self.version))]

    @staticmethod
    def scope_covered(state, states, username=None, *, analyzed=False, ignore_warnings=False):
        """只有同一筛选、读取方式及完整时间覆盖能替代原范围，不将统计当成分析。"""
        if state.get('pending_page'):
            return False
        for user in ([username] if username else state['conversations']):
            intervals = []
            for other in states:
                if user not in other['conversations'] or not other['read_complete'] or other.get('pending_page'):
                    continue
                if not ignore_warnings and other.get('warnings'):
                    continue
                if other.get('sender', '') != state.get('sender', '') or other.get('message_count') != state.get('message_count'):
                    continue
                if state.get('message_count') and set(other['conversations']) != set(state['conversations']):
                    continue
                if other['mode'] != state['mode'] or (analyzed and not other['complete_required']):
                    continue
                intervals.append((other['start'], other['end']))
            cursor = state['start']
            for lo, hi in sorted(intervals):
                if lo > cursor:
                    break
                cursor = max(cursor, hi)
            if not intervals or cursor < state['end']:
                return False
        return True

    @serialized
    def coverage(self):
        states = self.scopes()
        complete = bool(states) and all(self.scope_covered(s, states, analyzed=s['complete_required'] and s['mode'] != 'statistics') for s in states) and self.validate_complete()
        with self.service.store.connection() as db:
            analyzed, segments = self.service.workspace.saved_note_coverage(db, self.id, self.version)
            sender = "coalesce(nullif(json_extract(body,'$.sender_id'),''),json_extract(body,'$.media.senderUsername'),json_extract(body,'$.sender'),'')" if any(s.get('sender') for s in states) else "''"
            rows = [dict(r) for r in db.execute(f'SELECT username,time,{sender} sender_id FROM agent_material WHERE run_id=?', (self.id,))]
        coverage = []
        for username in dict.fromkeys(u for s in states for u in s['conversations']):
            relevant = [s for s in states if username in s['conversations']]
            count = sum(m['username'] == username and any(self.permits(s, m) for s in relevant) for m in rows)
            done = all(self.scope_covered(s, states, username, analyzed=s['complete_required'] and s['mode'] != 'statistics') for s in relevant)
            coverage.append({'username': username, 'read': count,
                'analyzed': min(count, analyzed.get(username, 0)),
                'read_complete': all(self.scope_covered(s, states, username, ignore_warnings=True) for s in relevant), 'complete': done,
                'warning': '；'.join(dict.fromkeys(w for s in relevant if not self.scope_covered(s, states, username) for w in s.get('warnings', [])))})
        run = self.guard()
        combined = [c['username'] for c in coverage]
        if states:
            self.service.update(self.id, query_scope=combined, query_filters={**(run.get('query_filters') or {}), 'conversations': combined})
        self.service.update(self.id, coverage_state='complete' if complete else 'partial', analysis={'complete': complete,
            'coverage': coverage, 'segments': segments,
            'tracked': any(s['complete_required'] and s['mode'] != 'statistics' for s in states)})

    def validate_complete(self, *, ignore_warnings=False):
        if self.topic is not None:
            return not requires_complete_analysis(self.guard().get('input_digest', '')) or self.topic.coverage()['complete']
        states = self.scopes()
        run = self.guard()
        if run.get('subtask_plan_version') == REVISION and not run.get('parent_run_id') and not self.service.planned_work.ready(run):
            return False
        if not states and (run.get('child_role') == 'range-analyst' or (not run.get('parent_run_id') and requires_complete_analysis(run.get('input_digest', '')))):
            return False
        needs_findings = run.get('child_role') == 'range-analyst' or (not run.get('parent_run_id') and requires_findings(run.get('input_digest', '')))
        if not ignore_warnings:
            for state in states:
                plan = self.get(state['analysis_plan']) if state.get('analysis_plan') else None
                if plan and plan['mode'] == 'parallel' and plan['phase'] != 'completed':
                    return False
        content_states = [s for s in states if s['mode'] != 'statistics' and s['complete_required']]
        if requires_complete_analysis(run.get('input_digest', '')) and run.get('required_conversations') and not set(run['required_conversations']) <= {u for s in (content_states if needs_findings else states) for u in s['conversations']}:
            return False
        # 统计只保证计数完成，不能替代范围分析员或完整报告的逐页分析。
        if needs_findings and not content_states:
            return False
        return all(not s['complete_required'] or self.scope_covered(s, states, analyzed=s['mode'] != 'statistics', ignore_warnings=ignore_warnings) for s in states)

    async def recent_page(self, state, capacity, *, probe_budget=None):
        run = self.guard()
        key = 'recent:' + state['handle']
        saved = self.get(key)
        if saved is None:
            result = await self.service.tools.recent_set(run['account'], state['conversations'], state['start'], state['end'],
                state['message_count'], self.guard, sender=state['sender'] or None)
            self.guard()
            self.save_messages([], result['messages'])
            saved = {'sources': [m['source'] for m in result['messages']], 'warning': result.get('warning', '')}
            self.put(key, 'deep_recent', saved)
        position, char_offset = (json.loads(state['cursor']) if state['cursor'] else [0, 0])
        return self.material_page(saved, position, char_offset, capacity, probe_budget=probe_budget)

    def material_page(self, saved, position, char_offset, capacity, *, probe_budget=None):
        """从已保存的有限候选集分片；最近 N 条和倒序检索共用，不截断原文。"""
        messages, originals, used = [], [], 2
        while position < len(saved['sources']):
            original = self.guard()['evidence'][saved['sources'][position]]
            text = original.get('text', '')
            remaining = capacity - used - size(message_payload({**original, 'text': ''})) - 256
            if remaining < 128 and messages:
                break
            lo, hi = 0, len(text) - char_offset
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if size(text[char_offset:char_offset + mid]) <= max(1, remaining):
                    lo = mid
                else:
                    hi = mid - 1
            if not lo and text[char_offset:]:
                raise ValueError('单条消息元数据超出预算')
            fragment = {**original, 'text': text[char_offset:char_offset + lo], 'text_offset': char_offset}
            originals.append(original)
            messages.append(fragment)
            used += size(message_payload(fragment))
            char_offset += lo
            if char_offset < len(text):
                fragment['next_text_offset'] = char_offset
                break
            position, char_offset = position + 1, 0
            if probe_budget is not None and used > probe_budget:
                break
        more = position < len(saved['sources'])
        return {'messages': messages, 'originals': originals, 'has_more': more,
            'next_cursor': json.dumps([position, char_offset]) if more else None, 'warning': saved['warning']}

    async def latest_page(self, state, capacity, *, kind=None, probe_budget=None):
        run = self.guard()
        cursor = json.loads(state['cursor']) if state.get('cursor') else {}
        batch_size = 1 if kind == 'file' else 20
        key = cursor.get('batch') or 'latest-batch:' + hashlib.sha256(
            json.dumps([state['handle'], kind, batch_size, cursor.get('offsets', {})], sort_keys=True).encode()).hexdigest()[:24]
        saved = self.get(key)
        if saved is None:
            result = await self.service.tools.latest(run['account'], state['conversations'], state['start'], state['end'],
                sender=state['sender'] or None, kind=kind, offsets=cursor.get('offsets', {}), limit=batch_size, checkpoint=self.guard)
            self.guard()
            stored = self.save_messages(result['messages'])
            saved = {'sources': [m['source'] for m in stored], 'warning': result.get('warning', ''),
                'has_more': result['has_more'], 'next_offsets': result['next_offsets']}
            self.put(key, 'deep_latest_batch', saved)
        position, char_offset = cursor.get('position', [0, 0])
        page = self.material_page(saved, position, char_offset, capacity, probe_budget=probe_budget)
        following = ({'batch': key, 'position': json.loads(page['next_cursor'])} if page['has_more'] else
            {'offsets': saved['next_offsets']} if saved['has_more'] else None)
        return {**page, 'has_more': following is not None,
            'next_cursor': json.dumps(following, sort_keys=True) if following is not None else None}

    async def search_page(self, state, query, offset, conversation_offset=0, retrieval_mode='keyword'):
        """主工具和固定查询分支共用范围筛选与任务内票据身份。"""
        run = self.guard()
        if retrieval_mode not in ('keyword', 'hybrid'):
            raise ValueError('retrieval_mode 必须为 keyword 或 hybrid')
        if offset < 0 or not 0 <= conversation_offset < len(state['conversations']):
            raise ValueError(f'offset 必须非负；conversation_offset 有效范围为 0 到 {len(state["conversations"]) - 1}')
        username = state['conversations'][conversation_offset]
        identity = {'run_id': self.id, 'version': self.version, 'account': run['account'],
            'scope_handle': state['handle'], 'query': query, 'retrieval_mode': retrieval_mode,
            'conversation': username, 'start': state['start'], 'end': state['end'], 'sender': state['sender']}
        key = 'search-ticket:' + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
        ticket = None
        if offset:
            saved = self.get(key)
            if not saved or saved.get('identity') != identity or (retrieval_mode == 'hybrid' and not saved.get('ticket')):
                raise ScopeError('search_restart_required', '检索续页身份或票据缺失，请保持原查询模式并从 offset=0 重新开始；已取得证据保留')
            ticket = saved['ticket']
        result = await self.service.tools.search(run['account'], username, query, state['start'], state['end'], offset,
            sender=state['sender'] or None, retrieval_mode=retrieval_mode, search_ticket=ticket, strict_paging=True)
        self.guard()
        if retrieval_mode == 'hybrid' and not result.get('search_ticket'):
            raise ScopeError('search_restart_required', '混合检索没有返回分页票据，请重新开始；已取得证据保留')
        self.put(key, 'deep_search_ticket', {'identity': identity, 'ticket': result.get('search_ticket')})
        return result

    def tools(self):
        if self.topic is not None:
            return self.topic.tools()
        @tool
        async def select_chat_scope(conversations: list[str] | None = None, all_chats: bool = False,
            time_phrase: str = '', start: ScopeTime = '', end: ScopeTime = '', sender: str | None = None, exclude: list[str] | None = None,
            complete: bool = False, mode: Literal['search', 'statistics'] = 'search',
            reuse_previous: Annotated[bool, Field(description='追问沿用上一轮查询条件时设为 true，由程序重建本轮范围，无需复制历史句柄或时间')] = False,
            message_count: Annotated[int | None, Field(description='仅用户明确要求最近 N 条时填写 N；全部范围必须为 null，绝非分页大小')] = None,
            clear_filters: Annotated[list[str] | None, Field(description='追问明确取消的条件：sender、message_count、time_range、conversations')] = None) -> dict:
            """选择查询范围。无时间要求才查全历史；相对时间可用最近N天/周/月，模糊时间先结合语境确定并说明假设。子任务省略日期和对象即可继承精确分配范围。普通问答 complete=false；完整分析 complete=true；计数 mode=statistics。"""
            return await self.select(conversations, all_chats, time_phrase, start, end, sender, exclude, complete, mode, reuse_previous, message_count, clear_filters)

        @tool
        async def search_messages(scope_handle: str, query: Annotated[str, Field(description='原词、编号、金额或待查的具体描述；概览请使用 read_messages')],
            offset: Annotated[int, Field(ge=0)] = 0, conversation_offset: Annotated[int, Field(ge=0)] = 0,
            retrieval_mode: Annotated[Literal['keyword', 'hybrid'], Field(description='明确原词、编号、金额用 keyword；模糊描述用 hybrid。续页保持查询与模式不变')] = 'keyword') -> dict:
            """在已选范围定位候选证据。按返回位置续页；指代、前提或状态变化用 read_context 核对。搜索不代表完整阅读。"""
            state = self.scope(scope_handle)
            if not query.strip():
                return {'messages': [], 'requires_query': True, 'next_tool': 'read_messages',
                    'scope_handle': scope_handle, 'instruction': '搜索需要具体关键词。概览或没有关键词时直接 read_messages，不要使用空关键词搜索。'}
            result = await self.search_page(state, query, offset, conversation_offset, retrieval_mode)
            self.guard()
            username = state['conversations'][conversation_offset]
            messages = [m for m in result.get('messages', []) if m['username'] in state['conversations'] and state['start'] <= m['time'] < state['end']
                and (not state['sender'] or state['sender'] == (m.get('sender_id') or m.get('sender')))]
            key = hashlib.sha256(json.dumps([scope_handle, query, retrieval_mode, conversation_offset, offset]).encode()).hexdigest()[:24]
            self.put('search-coverage:' + key, 'deep_search_coverage', {'scope_handle':scope_handle, 'query':query,
                'conversation':username, 'offset':offset, 'sources':[m['source'] for m in messages],
                'requested_retrieval_mode': retrieval_mode, 'retrieval_mode': result.get('retrieval_mode'),
                'has_more':bool(result.get('has_more')), 'warning':result.get('warning', ''),
                'freshness':result.get('freshness', {}), 'coverage':'search_only'})
            return {**{k: v for k, v in result.items() if k not in ('messages', 'originals', 'search_ticket')},
                'messages': self.save_messages(messages), 'coverage': 'search_only',
                'next_conversation_offset': conversation_offset if result.get('has_more') else conversation_offset + 1 if conversation_offset + 1 < len(state['conversations']) else None}


        @tool
        async def list_files(scope_handle: str) -> dict:
            """按最新到最早每次返回一个真实文件附件，本地过滤文字、图片和链接。先定位再 analyze_media；确需其他文件时才继续下一页。"""
            async with self.lock:
                state = self.scope(scope_handle)
                run = self.guard()
                if state['complete_required'] or state.get('message_count') or run.get('manifest_id') or run.get('work_query'):
                    raise ValueError('完整分析、最近 N 条或分配清单须用 read_messages 读取既定范围；文件列表不能替代覆盖要求')
                key = 'files:' + scope_handle
                position = self.get(key) or {'cursor': '', 'complete': False}
                if position['complete']:
                    return {'messages': [], 'has_more': False, 'scope_handle': scope_handle, 'order': 'newest_first',
                        'coverage': 'files_only', 'warning': position.get('warning', '')}
                result = await self.latest_page({**state, 'cursor': position['cursor']}, capacity(self.service, run), kind='file')
                payload = self.save_messages(result['messages'], result['originals'])
                warning = '；'.join(dict.fromkeys(w for w in (position.get('warning'), result.get('warning')) if w))
                self.put(key, 'deep_file_listing', {'cursor': result['next_cursor'], 'complete': not result['has_more'], 'warning': warning})
                return {'messages': payload, 'scope_handle': scope_handle, 'has_more': result['has_more'],
                    'order': 'newest_first', 'coverage': 'files_only', 'warning': warning,
                    'instruction': '仅包含真实文件附件，按最新优先。先分析符合问题的文件，证据足够即可回答；文件列表不表示读完全部聊天。'}

        @tool
        async def read_messages(scope_handle: str) -> dict:
            """读取范围下一页：普通问题从最新往前，查文件优先 list_files；完整分析按时间正序，每页先 commit_findings 再继续。"""
            state = self.scope(scope_handle)
            if state['mode'] == 'statistics':
                # 用户调用读取工具表达分析意图，派生同边界分析范围，不沿用统计游标。
                chosen = await self.select(conversations=state['conversations'],
                    start=datetime.fromtimestamp(state['start'], timezone.utc).isoformat(),
                    end=datetime.fromtimestamp(state['end'], timezone.utc).isoformat(),
                    sender=state['sender'], complete=True, message_count=state.get('message_count'))
                result = await self.read_next(chosen['scope_handle'])
                return {**result, 'instruction': '统计与分析分别计进度。已切换为相同边界的分析范围，后续使用本次返回的 scope_handle 和 page_id。'}
            return await self.read_next(scope_handle)

        @tool
        def commit_findings(scope_handle: str, page_id: str, findings: list[FindingInput]) -> dict:
            """保存本页分析。每项为 {text,sources:[消息编号],quote:可选直接引文}。无相关事实提交空列表；完整报告须处理每页。"""
            return self.commit(scope_handle, page_id, [f.model_dump(exclude_none=True) for f in findings])

        @tool
        async def read_context(scope_handle: str, source: str) -> dict:
            """回查已知来源前后文，返回仍在查询范围内的消息。"""
            state = self.scope(scope_handle)
            run = self.guard()
            original = run['evidence'].get(source)
            if not original or not self.permits(state, original):
                raise ValueError('来源不在当前范围')
            if run.get('manifest_id'):
                parent = self.service.guard(run['parent_run_id'])
                manifest = self.service.analysis_plans.get(parent, run['manifest_id'])
                refs = [r for r in manifest['core'] + manifest['context'] if r['username'] == original['username']]
                center = next((i for i, r in enumerate(refs) if r['source'] == source), None)
                if center is None:
                    raise ValueError('来源不属于分配清单')
                values = self.service.analysis_plans.messages(parent, refs[max(0, center - 10):center + 11])
                return {'messages': [message_payload(m) for m in values], 'data_source': 'assigned_manifest',
                    'instruction': '仅返回清单内正文及背景；范围外疑点交主任务核查。'}
            from fastapi import HTTPException
            try:
                result = await self.service.tools.context(run['account'], original)
            except HTTPException as exc:
                if exc.status_code not in (404, 410):
                    raise
                self.guard()
                return {'messages': self.save_messages([original]), 'context_available': False,
                    'warning': '当前数据源无法定位这条消息的前后文；保留已读取原文，不能据此推断缺失上下文。',
                    'data_source': 'saved_original', 'next_tool': 'read_messages', 'scope_handle': scope_handle}
            self.guard()
            messages = [m for m in result.get('messages', []) if m['username'] in state['conversations'] and state['start'] <= m['time'] < state['end']
                and (not state['sender'] or state['sender'] == (m.get('sender_id') or m.get('sender')))]
            return {'messages': self.save_messages(messages), 'warning': result.get('warning', '')}

        @tool
        async def count_messages(scope_handle: str, offset: int = 0) -> dict:
            """程序精确统计；首次遍历范围，后续 offset 分页回查已计算的日期和发言人分布，不重复读取。先选 mode=statistics。"""
            state = self.scope(scope_handle)
            if state['mode'] != 'statistics' or offset < 0:
                raise ValueError('请先选择统计范围')
            while not self.scope(scope_handle)['read_complete']:
                await self.read_next(scope_handle)
                await asyncio.sleep(0)
            self.service.update(self.id, statistics_scope=scope_handle)
            stats = self.service.workspace.statistics(self.id, offset=offset, limit=40)
            run = self.guard()
            refs = run.get('references', {})
            for item in [*stats['items'], *stats['sender_ranking']]:
                key = reference_id('person', run['account'], item.get('sender_id'))
                if key in refs:
                    item['person_reference'] = f'[[person:{key}]]'
            more = any(stats[k] for k in ('has_more', 'daily_has_more', 'sender_has_more'))
            current = self.scope(scope_handle)
            self.put('statistics-coverage:' + scope_handle, 'deep_statistics_coverage', {'scope_handle':scope_handle,
                'read_complete':current['read_complete'], 'warnings':current.get('warnings', []),
                'coverage':'statistics_only'})
            return {'statistics': stats, 'complete': current['read_complete'] and not current.get('warnings'),
                'analysis_complete': False, 'scope_handle': scope_handle,
                'instruction': 'complete 仅表示本范围计数已完成；分布分页按需读取。需要分析内容时调用 read_messages，统计不产生分析发现。',
                'warnings': current.get('warnings', []),
                'next_offset': offset + 40 if more else None,
                'continuation_tool': 'count_messages' if more else None,
                'sources': [message_payload(m, self.guard()['timezone_offset']) for m in self.guard()['evidence'].rows(limit=5)]}

        @tool
        def read_results(query: str = '', offset: int = 0, task_handle: str = '') -> dict:
            """分页回查本轮已分析发现，或用 query 搜索事实。"""
            self.guard()
            if task_handle:
                return self.service.planned_work.results(self, task_handle, offset, query)
            return self.service.workspace.page(self.id, self.version, 'finding', max(0, offset), 20, query)

        @tool
        def calculate_values(scope_handle: str, terms: list[CalculationTerm], operation: Literal['sum', 'difference', 'min', 'max'] = 'sum') -> dict:
            """同一单位的已确认事件交程序精确计算；difference 为第一项减去其余项。跨片关系、数值与方向未确认时不得计算。"""
            run = self.guard()
            if run.get('parent_run_id'):
                raise ValueError('子任务只提取局部事实；关联确认后的计算交主任务执行')
            state = self.scope(scope_handle)
            originals = run['evidence'].get_many(s for term in terms for s in term.sources)
            if any(not originals.get(s) or not self.permits(state, originals[s]) for term in terms for s in term.sources):
                raise ValueError('计算引用的来源不在当前已读取范围')
            result = calculate(terms, operation)
            key = hashlib.sha256(json.dumps([scope_handle, operation, [t.model_dump() for t in terms]], sort_keys=True).encode()).hexdigest()[:24]
            self.put('calculation:' + key, 'deep_calculation', {'terms':[t.model_dump() for t in terms], **result})
            return result

        @tool
        def search_material(scope_handle: str, query: str = '', source: str = '', offset: int = 0) -> dict:
            """回查本轮或同一 AI 对话旧轮的已保存原文；source 精确定位，query 搜索文字。返回真实来源，可用于追问。"""
            state = self.scope(scope_handle)
            run = self.guard()
            if offset < 0:
                raise ValueError('分页位置不能为负')
            if run.get('manifest_id'):
                values = [m for m in self.service.analysis_plans.assigned_messages(run, source) if query in m.get('text', '')]
                return {'messages': [message_payload(m) for m in values[offset:offset + 10]],
                    'has_more': offset + 10 < len(values), 'next_offset': offset + 10 if offset + 10 < len(values) else None}
            with self.service.store.connection() as db:
                candidates = db.execute("SELECT m.body FROM agent_material m JOIN records r ON r.kind='agent_run' AND r.id=m.run_id "
                    "WHERE r.account=? AND json_extract(r.body,'$.thread_id')=? AND (?='' OR m.source=?) AND (?='' OR instr(json_extract(m.body,'$.text'),?)>0) "
                    "AND m.username IN (SELECT value FROM json_each(?)) AND m.time>=? AND m.time<? "
                    "AND (?='' OR coalesce(json_extract(m.body,'$.sender_id'),json_extract(m.body,'$.sender'))=?) "
                    "GROUP BY m.source ORDER BY m.time DESC,m.source LIMIT 11 OFFSET ?", (run['account'], run['thread_id'], source, source, query, query,
                    json.dumps(state['conversations']), state['start'], state['end'], state['sender'], state['sender'], offset)).fetchall()
            values = [json.loads(row[0]) for row in candidates]
            values = [m for m in values if m['username'] in state['conversations'] and state['start'] <= m['time'] < state['end'] and
                (not state['sender'] or state['sender'] == (m.get('sender_id') or m.get('sender')))]
            selected = values[:10]
            fragments = [{**m, 'text': m.get('text', '')[:1000], 'next_text_offset': 1000 if len(m.get('text', '')) > 1000 else None} for m in selected]
            return {'messages': self.save_messages(fragments, selected), 'has_more': len(values) > 10,
                'next_offset': offset + len(selected) if len(values) > 10 else None}

        @tool
        def read_material(scope_handle: str, source: str, text_offset: int = 0) -> dict:
            """按字符位置续读已保存的长消息原文。next_text_offset 非空时可以继续，不重新查询聊天库。"""
            state = self.scope(scope_handle)
            original = self.guard()['evidence'].get(source)
            if text_offset < 0 or not original or not self.permits(state, original):
                raise ValueError('来源或续读位置不在当前范围')
            run = self.guard()
            if run.get('manifest_id'):
                values = self.service.analysis_plans.assigned_messages(run, source)
                for value in values:
                    start, end = value['text_offset'], value['next_text_offset']
                    if end <= text_offset and end != start:
                        continue
                    start = max(start, text_offset)
                    stop = min(end, start + 4000)
                    return {'messages': [message_payload({**value,
                        'text': value['text'][start - value['text_offset']:stop - value['text_offset']],
                        'text_offset': start, 'next_text_offset': stop if stop < end else None})]}
                raise ValueError('续读位置不属于分配正文或背景，跨片疑点交主任务核查')
            text = original.get('text', '')
            end = min(len(text), text_offset + 4000)
            return {'messages': self.save_messages([{**original, 'text': text[text_offset:end], 'text_offset': text_offset,
                'next_text_offset': end if end < len(text) else None}], [original])}

        @tool
        async def analyze_media(scope_handle: str, source: str, question: str = '',
            analysis_mode: Literal['overview', 'full'] = 'overview', cursor_handle: str = '') -> dict:
            """默认概览：提取有限正文，扫描件识别少量页面；文件用途/主题问题证据足够即可回答。需更多内容用返回的 next_cursor_handle 续读；明确要求全文逐图分析才选 full。不支持视觉时如实报告图片缺口。"""
            state = self.scope(scope_handle)
            run = self.guard()
            original = run['evidence'].get(source)
            if not original or not self.permits(state, original):
                raise ValueError('附件来源不在已选范围')
            media_cursor = None
            if cursor_handle:
                saved = self.get('media-cursor:' + cursor_handle)
                if (not saved or saved['source'] != source or saved['analysis_mode'] != analysis_mode
                        or (question and question != saved['question'])):
                    raise ValueError('附件续读位置不属于当前来源或分析模式，请使用对应结果的续读句柄')
                question, media_cursor = saved['question'], saved['cursor']
            if run.get('manifest_id'):
                assigned = self.service.analysis_plans.assigned_messages(run, source)
                if not assigned:
                    raise ValueError('附件来源不属于分配清单')
                # 媒体解析器可能把输入正文附在结果前，不能借此泄露未分配的长文。
                original = {**original, 'text': '\n'.join(m['text'] for m in assigned)}
            from .providers import audit_task_id, model_attempt_hook
            from .model_scheduler import subtask_id
            token = audit_task_id.set(run.get('parent_run_id') or run['id'])
            child_token = subtask_id.set(run['id'] if run.get('parent_run_id') else '')
            hook = model_attempt_hook.set(lambda: self.service.spend(self.id, 'models'))
            try:
                question = question or run.get('input_digest', '')
                options = {'media': True, 'question': question, 'skip_unsupported_images': True, 'analysis_mode': analysis_mode}
                if media_cursor is not None:
                    options['media_cursor'] = media_cursor
                enriched = await self.service.ai.media.enrich(run['account'], original, options, self.service.profile(run, True),
                    self.guard, unit_callback=lambda label, cached=False: None if cached else self.service.spend(self.id, 'media'),
                    progress_callback=MEDIA_PROGRESS.get())
                self.guard()
                # 媒体解释是派生结果，不能覆盖已经保存的原消息文本。
                key = hashlib.sha256(json.dumps([source, question, analysis_mode, media_cursor], sort_keys=True).encode()).hexdigest()[:24]
                detail = enriched.get('coverage_detail', {})
                body = {'source': source, 'question': question, 'analysis_mode': analysis_mode,
                    'analysis': enriched.get('text', ''), 'coverage': enriched.get('coverage', ''), 'coverage_detail': detail}
                self.put('media:' + key, 'deep_media_result', body)
                from .deep_backend import TaskBackend
                path = '/results/media/' + key + '.json'
                TaskBackend(self.service, self.id, self.version).write(path, json.dumps(body, ensure_ascii=False, indent=2))
                available = body['coverage'].startswith('已分析')
                next_handle = None
                if detail.get('next_cursor') is not None:
                    next_state = {'source': source, 'question': question, 'analysis_mode': analysis_mode, 'cursor': detail['next_cursor']}
                    next_handle = hashlib.sha256(json.dumps(next_state, sort_keys=True).encode()).hexdigest()[:24]
                    self.put('media-cursor:' + next_handle, 'deep_media_cursor', next_state)
                result = {'source': source, 'analysis': body['analysis'][:2000], 'result_path': path,
                    'analysis_truncated': len(body['analysis']) > 2000, 'analysis_mode': analysis_mode,
                    'coverage': body['coverage'], 'coverage_detail': {k: v for k, v in detail.items() if k != 'next_cursor'},
                    'next_cursor_handle': next_handle, 'available': available}
                if analysis_mode == 'overview' and available:
                    result['instruction'] = '基于已返回原文回答用途或主题，明确当前覆盖范围，不宣称已读全文；仅存在影响答案的缺口时续读。当前片段较长时先用 result_path 回查该片段完整内容。'
                if '已跳过图片内容' in body['coverage']:
                    result['note'] = '当前模型不支持图片，已跳过图片内容'
                    result['instruction'] = '向用户说明图片已跳过；继续分析已读取文字，不重试图片，不要求切换模型，不猜测图片内容。'
                    return result
                if not available:
                    result['note'] = body['coverage'] or '此媒体未能分析。'
                    result['instruction'] = ('当前片段没有可用正文，尚未读完文件；使用 next_cursor_handle 继续读取，不把空片段当作整个附件解析失败。'
                        if next_handle else '此媒体内容尚未确认，不要猜测。继续根据已读文字回答并说明缺口；本机文件缺失时需用户下载后才能分析，不要反复调用。')
                return result
            finally:
                audit_task_id.reset(token)
                subtask_id.reset(child_token)
                model_attempt_hook.reset(hook)

        @tool
        def plan_parallel_work(preliminary_analysis: str, evidence_handles: list[str], parallel_reason: str,
                main_work: MainWork, branches: list[BranchWork]) -> dict:
            """实际分析后规划独立分工；主模型保留具体工作，每轮最多三个分支。回执来自实际读取工具。不会启动 Agent。"""
            return self.service.planned_work.plan(self, preliminary_analysis, evidence_handles, parallel_reason, main_work, branches)

        @tool
        def record_main_analysis(plan_handle: str, analysis: str, sources: list[str]) -> dict:
            """保存主模型在并行期间完成的主线分析及真实来源；仅复述计划或等待分支不算完成。"""
            return self.service.planned_work.main_result(self, plan_handle, analysis, sources)

        @tool
        async def wait_subtasks(plan_handle: str, seen_handles: list[str] = []) -> dict:
            """主线工作已完成且确需依赖时等待新结果；事件驱动，无结果不唤醒模型。"""
            return await self.service.planned_work.wait(self, plan_handle, seen_handles)

        @tool
        def finish_parallel_work(plan_handle: str, synthesis: str, sources: list[str], gaps: list[str] = []) -> dict:
            """读取全部分支事实后保存主模型整合结论与缺口；并不替代全量覆盖校验。"""
            return self.service.planned_work.close(self, plan_handle, synthesis, sources, gaps)

        tools = [select_chat_scope, search_messages, list_files, read_messages, commit_findings, read_context, count_messages, read_results, calculate_values, search_material, read_material, analyze_media]
        # 构建旧检查点图时只读配置，不执行当前版本 guard；工具执行时仍逐次校验。
        run = self.service.run(self.id)
        if run.get('subtask_plan_version') == REVISION:
            if run.get('parent_run_id'):
                return [read_messages, commit_findings, read_context, read_material, analyze_media]
            tools.extend([plan_parallel_work, record_main_analysis, wait_subtasks, finish_parallel_work])
        return tools
