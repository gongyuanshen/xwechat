"""Publish complete, strictly validated labels from one audited model request."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from contextlib import aclosing

from langchain_core.messages.ai import add_usage
from langsmith import tracing_context
from pydantic import ValidationError

from .diagnostics import context as diagnostic_context, event as diagnostic_event
from .insight_schemas import LabelsOutput, MessageLabel
from .model_execution import call_policy, model_policy
from .model_scheduler import scheduler, subtask_id
from .providers import (ProviderFailure, analysis_messages, audit_task_id, capture_sdk_truncation,
                        check_output_refusal, model_attempt_hook)


def delivery_mode(profile):
    """Choose before requesting; a failed stream never changes this decision."""
    if profile.get('protocol') not in {'openai', 'anthropic'}:
        raise ValueError('该模型协议没有已实现的标签流式调用路径')
    streaming = profile.get('model_metadata', {}).get('streaming')
    if streaming is not None and type(streaming) is not bool:
        raise ValueError('模型 streaming 能力必须为布尔值')
    return 'batch' if streaming is False else 'stream'


class LabelOutputError(ProviderFailure):
    """Static, public validation details; never include model text."""
    def __init__(self, message, issue):
        super().__init__(message, reason='output_invalid')
        self.issue = issue


def _invalid(message='标签输出不符合严格格式、来源或证据要求。', issue='invalid_structure'):
    return LabelOutputError(message, issue)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _invalid()
        result[key] = value
    return result


class _Labels:
    """Frame objects without repairing JSON or interpreting partial strings."""
    opening = re.compile(r'\A\s*\{\s*"labels"\s*:\s*\[')

    def __init__(self, expected_sources, allowed_sources):
        self.expected = set(expected_sources)
        self.allowed = set(allowed_sources)
        if not self.expected or not self.expected <= self.allowed:
            raise ValueError('标签目标必须非空且属于允许引用的消息来源')
        self.raw, self.pos, self.state = '', 0, 'opening'
        self.start, self.depth, self.quoted, self.escaped = 0, 0, False, False
        self.seen = set()

    def feed(self, text):
        self.raw += text
        if self.state == 'opening':
            match = self.opening.match(self.raw)
            if not match:
                return
            self.pos, self.state = match.end(), 'value'
        while self.pos < len(self.raw):
            char = self.raw[self.pos]
            if self.state != 'object' and char in ' \r\n\t':
                self.pos += 1
                continue
            if self.state in ('value', 'next'):
                if char == ']' and self.state == 'value':
                    self.state = 'closing'
                elif char == '{':
                    self.start, self.depth, self.state = self.pos, 1, 'object'
                    self.quoted, self.escaped = False, False
                else:
                    raise _invalid()
            elif self.state == 'object':
                if self.quoted:
                    if self.escaped:
                        self.escaped = False
                    elif char == '\\':
                        self.escaped = True
                    elif char == '"':
                        self.quoted = False
                elif char == '"':
                    self.quoted = True
                elif char == '{':
                    self.depth += 1
                elif char == '}':
                    self.depth -= 1
                    if self.depth == 0:
                        raw = json.loads(self.raw[self.start:self.pos + 1], object_pairs_hook=_unique_object)
                        label = MessageLabel.model_validate(raw, strict=True).model_dump()
                        source, sources = label['source'], set(label['sources'])
                        known = label['emotion'] is not None or label['intent'] is not None
                        if source not in self.expected:
                            raise _invalid('标签目标不属于本批待识别消息。', 'unknown_target_source')
                        if source in self.seen:
                            raise _invalid('同一目标消息返回了重复标签。', 'duplicate_target_source')
                        if not sources <= self.allowed:
                            raise _invalid('标签证据引用了本次允许范围之外的来源。', 'unknown_evidence_source')
                        if known and source not in sources:
                            raise _invalid('非空情绪或意图标签未引用目标消息自身作为证据。', 'missing_self_evidence')
                        self.seen.add(source)
                        self.state = 'delimiter'
                        self.pos += 1
                        yield label
                        continue
            elif self.state == 'delimiter':
                if char == ',':
                    self.state = 'next'
                elif char == ']':
                    self.state = 'closing'
                else:
                    raise _invalid()
            elif self.state == 'closing':
                if char != '}':
                    raise _invalid('labels 数组结束后应为根 JSON 对象结束符 }，不允许额外字段、尾逗号或其他内容。', 'invalid_closing')
                self.state = 'done'
            else:
                raise _invalid('JSON 结束后出现多余内容，标签输出必须只有一个 JSON 对象。', 'trailing_content')
            self.pos += 1

    def finish(self):
        if self.state == 'opening':
            raise _invalid('输出开头不符合 labels JSON 对象格式，不能包含说明文字或 Markdown 包装。', 'invalid_opening')
        if self.state != 'done':
            raise ProviderFailure('模型标签 JSON 未完整结束，已保留完整标签。', reason='output_truncated')
        LabelsOutput.model_validate(json.loads(self.raw, object_pairs_hook=_unique_object), strict=True)
        if self.seen != self.expected:
            raise _invalid('标签输出遗漏了本批待识别消息。', 'missing_target_labels')


def _text(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise _invalid()
    text = []
    for block in content:
        if isinstance(block, str):
            text.append(block)
        elif isinstance(block, dict):
            if block.get('type') in {'text', 'output_text'} and 'text' in block:
                if not isinstance(block['text'], str):
                    raise _invalid()
                text.append(block['text'])
            elif block.get('type') == 'refusal':
                raise ProviderFailure('模型拒绝生成标签。', reason='output_refused')
        else:
            raise _invalid()
    return ''.join(text)


async def _responses(client, messages, options, mode, native_output):
    if mode == 'stream':
        async with aclosing(client.astream(messages, config={'callbacks': []}, **options)) as stream:
            async for response in stream:
                yield response
    elif native_output:
        bundle = await client.with_structured_output(LabelsOutput.model_json_schema(), method='json_schema',
            include_raw=True).ainvoke(messages, config={'callbacks': []}, **options)
        # The raw response goes through our parser, never the SDK's repaired object.
        yield bundle['raw']
        if bundle.get('parsing_error'):
            raise _invalid()
    else:
        yield await client.ainvoke(messages, config={'callbacks': []}, **options)


async def iter_labels(models, profile, prompt, expected_sources, *, allowed_sources, account=''):
    """Yield each valid target label; the caller persists it before requesting another.

    Profile selection and prompt construction remain with the insights service. A
    declared non-streaming model returns validated labels after its single batch.
    """
    from .agent_budget import active_budget, check_request, output_limit, ContextOverflow, is_context_error
    from .context_meter import active_meter

    mode = delivery_mode(profile)
    parser = _Labels(expected_sources, allowed_sources)
    with model_policy(strict_output=True):
        policy = call_policy.get()
        if policy.constrain_thinking:
            from .model_reasoning import constrain_reasoning_profile
            profile = constrain_reasoning_profile(profile)
        messages = analysis_messages(prompt, LabelsOutput)
        metadata = profile.get('model_metadata', {})
        native_output = mode == 'batch' and profile['protocol'] == 'anthropic' and metadata.get('structured_output') is True
        extra = LabelsOutput.model_json_schema() if native_output else None
        if active_budget.get():
            check_request(profile, messages, extra)
        hook = model_attempt_hook.get()
        if hook:
            hook()
        started, queued = time.time(), time.monotonic()
        requested = None
        audit = {'id': uuid.uuid4().hex, 'profile_id': profile['id'], 'profile_name': profile.get('name', ''),
                 'model': profile.get('model', ''), 'provider': profile.get('provider', ''),
                 'profile_revision': profile.get('revision'), 'account': account, 'task_id': audit_task_id.get(),
                 'subtask_id': subtask_id.get(), 'attempt': 1, 'started_at': started, 'image_count': 0,
                 'status': 'running', 'usage': {}, 'usage_known': False, 'labels_emitted': 0,
                 'labels_expected': len(parser.expected), 'delivery_mode': mode,
                 'timeout_seconds': policy.seconds, 'received_chunks': 0, 'last_response_ms': None}
        audit.update({k: v for k, v in diagnostic_context.get().items()
                      if k in {'trace_id', 'operation_id', 'execution_id', 'run_id', 'thread_id'}})
        models.store.put('usage', audit, account=account)
        diagnostic_event('model.call.started', call_id=audit['id'], attempt=1, image_count=0)
        timeout = asyncio.timeout(policy.seconds)
        timeout_phase = 'queue'
        try:
            terminal = mode == 'batch'
            async with timeout, models.semaphore:
                requested = time.monotonic()
                timeout_phase = 'batch_total' if mode == 'batch' else 'provider_io'
                if mode == 'stream':
                    timeout.reschedule(None)
                diagnostic_event('model.call.acquired', call_id=audit['id'], queue_ms=(requested - queued) * 1000)
                options = {}
                if active_budget.get():
                    options['max_tokens'] = output_limit(profile)
                if policy.output_tokens is not None:
                    options['max_tokens'] = min(output_limit(profile), max(policy.output_tokens,
                        (profile.get('thinking_budget') or 0) + 1))
                if profile['protocol'] == 'openai':
                    if metadata.get('structured_output') is True:
                        options['response_format'] = {'type': 'json_object'}
                audit['output_format'] = 'json_schema' if native_output else options.get('response_format', {}).get('type', 'text')
                with tracing_context(enabled=False):
                    client = models.client(profile)
                    if profile['protocol'] == 'openai' and mode == 'stream':
                        from langchain_openai import ChatOpenAI
                        if isinstance(client, ChatOpenAI):
                            # Client configuration is consumed by Chat Completions;
                            # the Responses SDK does not accept a stream_usage kwarg.
                            client = client.model_copy(update={'stream_usage': True})
                        else:
                            # The existing ReasoningClient maps this option itself.
                            options['stream_usage'] = True
                    async with aclosing(_responses(client, messages, options, mode, native_output)) as responses:
                        while True:
                            if mode == 'stream':
                                timeout_phase = 'stream_idle' if audit['received_chunks'] else 'first_response'
                                timeout.reschedule(asyncio.get_running_loop().time() + policy.seconds)
                            try:
                                response = await anext(responses)
                            except StopAsyncIteration:
                                if mode == 'stream':
                                    timeout.reschedule(None)
                                break
                            if mode == 'stream':
                                # The caller persists each yielded label in this task;
                                # its work is not time spent awaiting a provider response.
                                timeout.reschedule(None)
                            audit['received_chunks'] += 1
                            audit['last_response_ms'] = (time.monotonic() - requested) * 1000
                            usage = getattr(response, 'usage_metadata', None)
                            if usage:
                                audit.update(usage=add_usage(audit['usage'], usage), usage_known=True)
                            response_metadata = response.response_metadata
                            finish = response_metadata.get('finish_reason') or response_metadata.get('stop_reason')
                            status = response_metadata.get('status')
                            check_output_refusal(response, audit)
                            if status in {'failed', 'cancelled'}:
                                raise ProviderFailure('模型服务未完成本次响应，已保留先前完成的标签。',
                                                      reason='provider_' + status)
                            if (getattr(response, 'tool_calls', None) or getattr(response, 'tool_call_chunks', None)
                                    or finish in {'tool_calls', 'function_call'}):
                                raise _invalid('模型返回了工具调用，而不是所请求的消息标签。', 'unexpected_tool_call')
                            for label in parser.feed(_text(response.content)):
                                audit['labels_emitted'] += 1
                                yield label
                            if finish in {'length', 'max_tokens'} or status == 'incomplete':
                                audit['finish_reason'] = finish or 'incomplete'
                                raise ProviderFailure('模型输出被截断，已保留完整标签。', reason='output_truncated')
                            if finish in {'stop', 'end_turn'} or status == 'completed':
                                terminal = True
                                audit['finish_reason'] = finish or status
            if not terminal:
                raise ProviderFailure('模型流缺少正常结束事件，已保留完整标签。', reason='output_truncated')
            parser.finish()
            meter = active_meter.get()
            if meter:
                meter.observe(profile, messages, extra, audit['usage'])
                audit['context_measurement'] = dict(meter.last)
            audit['status'] = 'success'
        except (asyncio.CancelledError, GeneratorExit):
            audit['status'] = 'cancelled'
            raise
        except Exception as exc:
            status = getattr(exc, 'status_code', None)
            if status == 429:
                scheduler().throttled()
            if capture_sdk_truncation(audit, exc):
                reason = 'output_truncated'
            elif isinstance(exc, ProviderFailure) and exc.reason:
                reason = exc.reason
            elif isinstance(exc, (ValueError, ValidationError)):
                reason = 'output_invalid'
            elif isinstance(exc, TimeoutError) or 'timeout' in type(exc).__name__.lower():
                reason = 'timeout'
            elif isinstance(exc, ContextOverflow) or is_context_error(exc):
                reason = 'context_overflow'
            else:
                reason = f'http_{status}' if type(status) is int else 'provider_error'
            audit.update(status='failed', error_type=type(exc).__name__, error_code=reason, http_status=status)
            detail = ''
            if isinstance(exc, LabelOutputError):
                audit['output_issue'] = exc.issue
                detail = str(exc)
            elif reason == 'output_refused':
                detail = '模型服务拒绝处理本批消息（内容过滤或拒绝响应），已保留先前完成的标签；未自动重试或跳过。'
            elif reason in {'provider_failed', 'provider_cancelled'}:
                detail = '模型服务返回失败或取消状态，已保留先前完成的标签。'
            elif reason == 'timeout':
                audit.update(timeout_phase=timeout_phase if timeout.expired() else 'provider_io',
                             timeout_seconds=policy.seconds if timeout.expired() else None)
                seconds = f'{policy.seconds:g}'
                if audit['timeout_phase'] == 'queue':
                    detail = f'等待模型执行额度超过 {seconds} 秒，尚未发送请求。'
                elif audit['timeout_phase'] == 'first_response':
                    detail = f'等待模型首次响应超过 {seconds} 秒。'
                elif audit['timeout_phase'] == 'stream_idle':
                    detail = f'模型连续 {seconds} 秒未返回新响应。'
                elif audit['timeout_phase'] == 'batch_total':
                    detail = f'非流式标签调用总耗时超过 {seconds} 秒（含排队）。'
                else:
                    detail = '模型服务或网络读取超时。'
                detail += '已保留已完成标签，未自动重试或跳过。'
            elif isinstance(exc, ValidationError):
                # Match the provider audit policy: field names/types only, never values or unknown keys.
                allowed = set(MessageLabel.model_fields) | {'labels'}
                audit['validation_errors'] = [
                    {'path': [part if isinstance(part, int) or part in allowed else '<field>' for part in error['loc']],
                     'type': error['type']}
                    for error in exc.errors(include_input=False, include_context=False)[:12]]
                detail = '标签字段校验失败；具体字段和约束见调用审计。'
            diagnostic_event('model.call.attempt_failed', level=logging.WARNING, error=exc,
                             call_id=audit['id'], diagnostic_id=audit['id'],
                             timeout_phase=audit.get('timeout_phase'), timeout_seconds=audit['timeout_seconds'],
                             received_chunks=audit['received_chunks'], last_response_ms=audit['last_response_ms'])
            raise ProviderFailure(f'严格标签调用失败（{reason}）；{detail}请查看调用审计。',
                                  authentication=status in {401, 403}, reason=reason) from None
        finally:
            audit.update(finished_at=time.time(), duration_ms=round((time.time() - started) * 1000),
                         response_chars=len(parser.raw))
            models.store.put('usage', audit, account=account)
            diagnostic_event('model.call.finished', level=logging.ERROR if audit['status'] == 'failed' else logging.INFO,
                call_id=audit['id'], status=audit['status'], duration_ms=audit['duration_ms'],
                usage_known=audit['usage_known'], input_tokens=audit['usage'].get('input_tokens'),
                output_tokens=audit['usage'].get('output_tokens'), http_status=audit.get('http_status'),
                queue_ms=(requested - queued) * 1000 if requested is not None else None,
                request_ms=(time.monotonic() - requested) * 1000 if requested is not None else None,
                timeout_phase=audit.get('timeout_phase'), timeout_seconds=audit['timeout_seconds'],
                received_chunks=audit['received_chunks'], last_response_ms=audit['last_response_ms'],
                validation_status='success' if audit['status'] == 'success' else 'failed')
