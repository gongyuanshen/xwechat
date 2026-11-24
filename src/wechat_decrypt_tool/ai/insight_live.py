"""Opt-in, serial message recognition using canonical messages and durable labels."""
from __future__ import annotations

import asyncio
from collections import Counter
import copy
from contextlib import aclosing
import hashlib
import json
import logging
import time
import uuid

from pydantic import ValidationError

from .diagnostics import event
from .insight_schemas import LiveBatchInput, LiveScope, LiveSettingsInput, LiveStateInput, LabelsOutput, MessageLabel
from .agent_budget import output_limit
from .model_execution import model_policy
from .model_reasoning import explicit_reasoning
from .model_selection import SelectedModel, selected_model
from .providers import ProviderFailure, audit_task_id, public_profile
from ..app_paths import get_output_dir
from ..account_workers import account_to_thread
from ..snapshot_registry import account_work, create_account_task


ACTIVE = {'queued', 'running'}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def model_source(model):
    # Model parameters affecting predictions are part of provenance; never include credentials.
    return {key: model.get(key) for key in ('id', 'provider', 'model', 'revision', 'protocol',
        'base_url', 'reasoning_effort', 'thinking_mode', 'thinking_budget')}


def self_username(account):
    from ..account_identity import resolve_account_self_username
    from ..chat_helpers import _resolve_account_dir
    account_dir = _resolve_account_dir(account)
    return resolve_account_self_username(account_dir)


class LiveCancelled(Exception):
    pass


class InsightLiveService:
    def __init__(self, parent):
        self.parent = parent
        self.store = parent.store
        self.jobs = {}
        self.locks = {}

    def scope(self, options):
        from .insights import insight_profile
        from .insight_local_fine import FINE_VERSION
        from .insight_local_models import SPEC
        value = LiveScope.model_validate({k: options[k] for k in LiveScope.model_fields if k in options}).model_dump()
        if value['engine'] == 'laya':
            profile = None
            model = dict(provider='local', model=SPEC['id'], revision=SPEC['revision'], fine_version=FINE_VERSION)
        else:
            choice = value['selected_model'] or selected_model(self.store)
            if not choice or choice.get('unavailable'):
                raise ProviderFailure('请先选择可用的 AI 模型')
            choice = SelectedModel.model_validate(choice).model_dump()
            value['selected_model'] = choice
            profile = insight_profile(self.parent.models.resolve_turn(choice['profile_id'], choice['model_id'],
                reasoning_effort=choice['reasoning_effort'], thinking_mode=choice['thinking_mode'],
                thinking_budget=choice['thinking_budget']))
            model = public_profile(profile)
        scope_id = digest([value['account'], value['username'], value['engine'], model_source(model),
                           FINE_VERSION if value['engine'] == 'laya' else 'api-labels-v2'])
        return value | dict(id=scope_id, scope_id=scope_id, model=model,
            self_username=self_username(value['account']) if value['username'].endswith('@chatroom') else None), profile

    def _cached(self, scope, ref):
        item = self.store.get('insight_live_label', digest([scope['id'], ref['identity']]))
        if item and (item['sender_id'] == scope['self_username'] or
                not scope['username'].endswith('@chatroom') and item['sender_id'] != scope['username']):
            return None
        return item if item and not item.get('invalidated') and item['fingerprint'] == ref['fingerprint'] and item['time'] == ref['time'] else None

    def state(self, options):
        data = LiveStateInput.model_validate(options).model_dump()
        with account_work(get_output_dir() / 'databases' / data['account']):
            scope, _ = self.scope(data)
            # A changed visible original invalidates every presentation of its old conclusion,
            # including header and batch restoration. Keep evidence; only a new analysis replaces it.
            with self.store.lock:
                for ref in data['messages']:
                    old = self.store.get('insight_live_label', digest([scope['id'], ref['identity']]))
                    if old and (old['fingerprint'] != ref['fingerprint'] or old['time'] != ref['time']):
                        self.store.put('insight_live_label', old | dict(invalidated=True), account=scope['account'])
            saved = self.store.get('insight_live_scope', scope['id'])
            batch = self.get_batch(saved['last_batch_id'], scope['account']) if saved and saved.get('last_batch_id') else None
            return dict(scope_id=scope['id'], enabled=bool(saved and saved['enabled']),
                items=[item for ref in data['messages'] if (item := self._cached(scope, ref)) is not None],
                batch=batch, mood=self.mood(scope))

    async def settings(self, options):
        data = LiveSettingsInput.model_validate(options).model_dump()
        with account_work(get_output_dir() / 'databases' / data['account']):
            scope, _ = self.scope(data)
            with self.parent.ai.account_lifecycle_lock, self.store.lock:
                if self.parent.stopping:
                    raise ValueError('分析服务正在停止')
                self.parent.ai.deleted_accounts.discard(scope['account'])
                self.store.revoked_accounts.discard(scope['account'])
                previous = self.store.get('insight_live_scope', scope['id']) or {}
                self.store.put('insight_live_scope', scope | dict(enabled=data['enabled'],
                    last_batch_id=previous.get('last_batch_id')), account=scope['account'])
            if not data['enabled']:
                for batch in self._batches(scope['account']):
                    if batch['scope_id'] == scope['id'] and batch['status'] in ACTIVE:
                        await self.cancel(batch['id'], scope['account'])
            return self.state({k: scope[k] for k in LiveScope.model_fields})

    def create_batch(self, options):
        from .insight_label_stream import delivery_mode
        data = LiveBatchInput.model_validate(options).model_dump()
        with account_work(get_output_dir() / 'databases' / data['account']):
            scope, profile = self.scope(data)
            if scope['engine'] == 'laya':
                self.parent.local_models.require_ready()
            mode = 'local' if profile is None else delivery_mode(profile)
            with self.parent.ai.account_lifecycle_lock, self.store.lock:
                saved = self.store.get('insight_live_scope', scope['id'])
                if self.parent.stopping or scope['account'] in self.store.revoked_accounts:
                    raise ValueError('分析服务正在停止或账号数据已清除')
                if not saved or not saved['enabled']:
                    raise ValueError('请先开启当前会话的意图识别')
                previous = self.store.get('insight_live_batch', saved['last_batch_id']) if saved.get('last_batch_id') else None
                if previous and previous['status'] in ACTIVE:
                    raise ValueError('当前模型已有批次正在分析，请等待完成')
                if previous and previous['status'] == 'failed' and not data['retry']:
                    raise ValueError('上个批次失败，自动分析已停止；请明确点击重试')
                refs = [ref for ref in data['messages'] if self._cached(scope, ref) is None]
                batch = self.store.put('insight_live_batch', scope | dict(id=uuid.uuid4().hex,
                    messages=refs, requested_messages=data['messages'], context=data['context'], status='queued', created=time.time(),
                    data_source='snapshot', delivery_mode=mode,
                    progress=dict(total=len(refs), analyzed=0), error=None, context_task_ids=[]), account=scope['account'])
                saved['last_batch_id'] = batch['id']
                self.store.put('insight_live_scope', saved, account=scope['account'])
                self.jobs[batch['id']] = create_account_task(get_output_dir() / 'databases' / scope['account'],
                        self.execute, batch['id'], copy.deepcopy(profile))
            self._event(batch)
            return self.get_batch(batch['id'], scope['account'])

    def _batches(self, account=None):
        return self.store.list('insight_live_batch', account)

    def get_batch(self, id, account):
        batch = self.store.get('insight_live_batch', id)
        if batch is None or batch['account'] != account:
            raise KeyError(id)
        scope = dict(id=batch['scope_id'], account=account, username=batch['username'],
            self_username=self_username(account) if batch['username'].endswith('@chatroom') else None)
        return batch | dict(items=[item for ref in batch.get('requested_messages', batch['messages'])
            if (item := self._cached(scope, ref)) is not None], mood=self.mood(scope))

    def _event(self, batch, **extra):
        self.store.event(batch['account'], 'insight_live', dict(scope_id=batch['scope_id'],
            batch_id=batch['id'], status=batch['status'], progress=batch['progress'], error=batch['error'], **extra))

    def _update(self, id, **fields):
        with self.store.lock:
            batch = self.store.get('insight_live_batch', id)
            if batch is None or batch['account'] in self.store.revoked_accounts:
                return
            batch = self.store.put('insight_live_batch', batch | fields | dict(updated_at=time.time()),
                                   account=batch['account'])
        self._event(batch)

    def _check(self, id):
        batch = self.store.get('insight_live_batch', id)
        if batch is None or self.parent.stopping or batch['status'] not in ACTIVE or batch['account'] in self.store.revoked_accounts:
            raise LiveCancelled()
        scope = self.store.get('insight_live_scope', batch['scope_id'])
        if not scope or not scope['enabled']:
            raise LiveCancelled()
        return batch

    def _read(self, batch):
        refs = {ref['identity']: ref for ref in [*batch['context'], *batch['messages']]}
        if not refs:
            return [], []
        times = [ref['time'] for ref in refs.values()]
        pages = self.parent.reader(batch['account'], batch['username'], min(times), max(times),
            page_size=100,
            checkpoint=lambda: self._check(batch['id']))
        material = {}
        try:
            for page in pages:
                self._check(batch['id'])
                expected = 'decrypted' if batch['data_source'] == 'snapshot' else 'realtime'
                if page['source'] != expected or page.get('warning'):
                    raise ValueError('聊天数据来源与提交时不一致，自动识别已停止')
                for msg in page['messages']:
                    ref = refs.get(msg['identity'])
                    if ref is None:
                        continue
                    if msg['media']['isSent']:
                        raise ValueError('意图识别不分析本人消息，请刷新聊天后重试')
                    if not batch['username'].endswith('@chatroom') and msg['sender_id'] != batch['username']:
                        raise ValueError('私聊意图识别仅支持对方消息，请刷新聊天后重试')
                    content = msg['media'].get('content', '')
                    if (msg['kind'] not in {'text', 'quote'} or not isinstance(content, str) or
                        not content.strip() or (msg['kind'] == 'quote' and content == '[引用消息]')):
                        raise ValueError('请求的消息不再是可分析文本，请刷新聊天')
                    fingerprint = hashlib.sha256(content.encode()).hexdigest()
                    if fingerprint != ref['fingerprint'] or msg['time'] != ref['time']:
                        raise ValueError('消息原文或时间已变化，请刷新聊天后重试')
                    item = {k: msg[k] for k in ('source', 'identity', 'anchor', 'time', 'sender_id', 'sender')}
                    item.update(username=batch['username'], text=content, fingerprint=fingerprint,
                        target=batch['username'].endswith('@chatroom') or msg['sender_id'] == batch['username'],
                        quote_context=msg['media'].get('quoteContent', ''))
                    if ref['identity'] in material and material[ref['identity']] != item:
                        raise ValueError('消息身份对应多份不同原文，自动识别已停止')
                    material[ref['identity']] = item
        finally:
            pages.close()
        if set(material) != set(refs):
            raise ValueError('部分消息已无法从当前聊天数据源读取，请刷新聊天后重试')
        targets = sorted((material[ref['identity']] for ref in batch['messages']), key=lambda x: (x['time'], x['identity']))
        context = sorted((material[ref['identity']] for ref in batch['context']), key=lambda x: (x['time'], x['identity']))
        return targets, context

    def portraits(self, scope, items):
        """Latest saved sender portrait from this account, chat and model; never group-as-person."""
        senders = {item['sender_id'] for item in items}
        with self.store.connection() as db:
            rows = db.execute("""SELECT body FROM records WHERE kind='insight_task' AND account=?
                AND json_extract(body,'$.username')=? AND json_type(body,'$.portrait')='object'
                AND coalesce(json_extract(body,'$.engine'),'api')=? ORDER BY updated DESC""",
                (scope['account'], scope['username'], scope['engine'])).fetchall()
        result = {}
        for row in rows:
            task = json.loads(row[0])
            if model_source(task['model']) != model_source(scope['model']):
                continue
            subject = task['member_username'] if scope['username'].endswith('@chatroom') else scope['username']
            if subject not in senders or subject in result:
                continue
            value = task['portrait']
            result[subject] = json.dumps(dict(task_id=task['id'], sender_id=subject,
                start=task['start'], end=task['end'], summary=value['summary']['text'],
                communication=[entry['text'] for entry in value['communication']],
                uncertain=value['uncertain']), ensure_ascii=False)
        return result

    def mood(self, scope):
        group = scope['username'].endswith('@chatroom')
        with self.store.connection() as db:
            rows = db.execute("""SELECT body,updated FROM records WHERE kind='insight_live_label' AND account=?
                AND json_extract(body,'$.scope_id')=? AND (? OR json_extract(body,'$.sender_id')=?)
                AND json_extract(body,'$.sender_id') IS NOT ?
                ORDER BY json_extract(body,'$.time') DESC,json_extract(body,'$.identity') DESC LIMIT ?""",
                (scope['account'], scope['id'], group, scope['username'], scope['self_username'], 20 if group else 1)).fetchall()
        if not rows:
            return None
        items = [json.loads(row[0]) for row in rows]
        counts = Counter(item['label']['emotion'] for item in items
                         if not item.get('invalidated') and item['label']['emotion'] is not None)
        ranking = counts.most_common()
        dominant = ranking[0][0] if ranking and ranking[0][1] * 2 >= len(items) and (
            len(ranking) == 1 or ranking[0][1] > ranking[1][1]) else None
        return dict(kind='group' if group else 'person', label=dominant, sample_count=len(items),
            known_count=sum(counts.values()), time=items[0]['time'],
            revision=max(row['updated'] for row in rows),
            sources=[item['source'] for item in items if not item.get('invalidated')])

    def _save(self, batch, item, label, allowed, raw=None):
        label = MessageLabel.model_validate(label).model_dump()
        if label['source'] != item['source'] or not set(label['sources']) <= allowed:
            raise ValueError('消息标签引用了输入范围外的消息')
        with self.store.lock:
            current = self._check(batch['id'])
            record = self.store.put('insight_live_label', item | dict(scope_id=batch['scope_id'],
                batch_id=batch['id'], account=batch['account'], label=label, local_analysis=raw,
                invalidated=False,
                emotion=label['emotion'], intent=label['intent'], reason=label['reason'], sources=label['sources']),
                id=digest([batch['scope_id'], item['identity']]), account=batch['account'])
            self._update(batch['id'], progress=dict(total=current['progress']['total'], analyzed=current['progress']['analyzed'] + 1))
        self._event(self._check(batch['id']), label=record, mood=self.mood(dict(id=batch['scope_id'],
            account=batch['account'], username=batch['username'], self_username=batch['self_username'])))

    async def _thread(self, account, function, *args, **kwargs):
        operation = asyncio.create_task(account_to_thread(get_output_dir() / 'databases' / account, function, *args, **kwargs))
        try:
            return await asyncio.shield(operation)
        finally:
            if not operation.done():
                await operation  # Reader/ONNX worker must release its own resources before cancellation completes.

    async def execute(self, id, profile):
        from .insights import LABEL_INSTRUCTION, InsightOutputError, model_message
        from .insight_label_stream import iter_labels
        token = audit_task_id.set(id)
        try:
            batch = self._check(id)
            lock = self.locks.setdefault((batch['account'], batch['username']), asyncio.Lock())
            async with lock:
                batch = self._check(id)
                self._update(id, status='running')
                items, context = await self._thread(batch['account'], self._read, batch)
                self._check(id)
                priors = self.portraits(batch, items)
                self._update(id, context_task_ids=[json.loads(value)['task_id'] for value in priors.values()])
                allowed = {item['source'] for item in [*context, *items]}
                if batch['engine'] == 'laya' and items:
                    from .laya_runtime import LayaRuntime
                    from .insight_local_fine import predict_message
                    async with self.parent.local_lock:
                        self._check(id)
                        runtime = LayaRuntime(self.parent.local_models.require_ready())
                        try:
                            for item in items:
                                self._check(id)
                                result = await self._thread(batch['account'], predict_message, runtime, item, context,
                                    priors.get(item['sender_id']), private_chat=not batch['username'].endswith('@chatroom'))
                                self._save(batch, item, result['label'], allowed, result['raw'])
                                context = (context + [item])[-3:]
                        finally:
                            runtime.close()
                elif items:
                    data = dict(messages=[model_message(item) for item in items],
                        context=[model_message(item) for item in context], portraits=priors)
                    self.parent._check_prompt(profile, LABEL_INSTRUCTION, data, LabelsOutput)
                    by_source = {item['source']: item for item in items}
                    stream = iter_labels(self.parent.models, profile, self.parent._prompt(LABEL_INSTRUCTION, data),
                        set(by_source), allowed_sources=allowed, account=batch['account'])
                    with model_policy(strict_output=True, output_tokens=min(4096, output_limit(profile)),
                                      constrain_thinking=not explicit_reasoning(profile)):
                        async with aclosing(stream):
                            async for label in stream:
                                self._save(batch, by_source[label['source']], label, allowed)
                self._check(id)
                self._update(id, status='completed')
        except (LiveCancelled, asyncio.CancelledError):
            self._update(id, status='cancelled')
        except Exception as exc:
            diagnostic_id = uuid.uuid4().hex
            event('insight.live.failed', level=logging.ERROR, error=exc, task_id=id, diagnostic_id=diagnostic_id)
            code = ('INSIGHT_MODEL_REFUSED' if isinstance(exc, ProviderFailure) and exc.reason == 'output_refused' else
                    'INSIGHT_INVALID_OUTPUT' if isinstance(exc, (ValidationError, InsightOutputError)) or
                    isinstance(exc, ProviderFailure) and exc.reason.startswith('output_') else
                    'INSIGHT_MODEL_FAILED' if isinstance(exc, ProviderFailure) else 'INSIGHT_LIVE_FAILED')
            message = str(exc) if isinstance(exc, (ValueError, ProviderFailure)) and not isinstance(exc, ValidationError) else '消息识别失败，请查看诊断编号'
            self._update(id, status='failed', error=dict(code=code, message=message, diagnostic_id=diagnostic_id))
        finally:
            audit_task_id.reset(token)
            self.jobs.pop(id, None)

    async def cancel(self, id, account):
        batch = self.get_batch(id, account)
        if batch['status'] in ACTIVE:
            self._update(id, status='cancelled')
            job = self.jobs.get(id)
            if job:
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)
                self.jobs.pop(id, None)
        return self.get_batch(id, account)

    def cancel_account(self, account):
        for batch in self._batches(account):
            if batch['status'] in ACTIVE:
                self._update(batch['id'], status='cancelled')
                job = self.jobs.get(batch['id'])
                if job:
                    job.get_loop().call_soon_threadsafe(job.cancel)

    def start(self):
        for batch in self._batches():
            if batch['status'] in ACTIVE and batch['id'] not in self.jobs:
                self._update(batch['id'], status='failed', error=dict(code='INSIGHT_INTERRUPTED',
                    message='应用退出中断了识别；已完成标签保留，请点击重试', diagnostic_id=batch['id']))

    async def stop(self):
        interrupted = list(self.jobs.items())
        for id, job in interrupted:
            self._update(id, status='cancelled')
            job.cancel()
        await asyncio.gather(*(job for _, job in interrupted), return_exceptions=True)
        for id, _ in interrupted:
            self._update(id, status='failed', error=dict(code='INSIGHT_INTERRUPTED',
                message='应用退出中断了识别；已完成标签保留，请点击重试', diagnostic_id=id))
        self.jobs.clear()
