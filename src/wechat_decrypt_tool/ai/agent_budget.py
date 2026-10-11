"""Agent 请求预算；未知分词器使用 UTF-8 字节上界，避免按模型名称猜窗口。"""
import json
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

from .providers import ProviderFailure

active_budget = ContextVar('agent_input_budget', default=None)
MAX_READ_MESSAGES = 1000


class ContextOverflow(ProviderFailure):
    pass


def size(value):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, default=str)
    return len(value.encode('utf-8'))


def input_limit(profile, *, output_tokens=None):
    window = profile.get('context_window')
    limit = active_budget.get() or profile.get('input_budget') or window or 32768
    if window:
        reserve = model_output_limit(profile) if output_tokens is None else output_tokens
        limit = min(limit, max(512, int(window) - reserve - 512))
    upstream_input = profile.get('model_metadata', {}).get('limit', {}).get('input')
    if isinstance(upstream_input, int) and upstream_input > 0:
        limit = min(limit, upstream_input)
    return int(limit)


def output_limit(profile):
    output = profile.get('model_metadata', {}).get('limit', {}).get('output')
    if output is None:
        from .model_catalog import documented_metadata
        output = documented_metadata(profile, profile.get('model', '')).get('limit', {}).get('output')
    if isinstance(output, int) and output > 0:
        # 输入输出共享窗口时，为请求正文留出空间。
        return min(output, max(256, int(profile.get('context_window') or output * 2) // 2))
    return min(4096, max(256, int(profile.get('context_window') or 32768) // 4))


def model_output_limit(profile):
    """实际请求参数与输入预留使用同一出口，不按模型最大输出能力虚留空间。"""
    return min(output_limit(profile), 8192)


def request_size(messages, extra=None):
    # 计入消息封装与工具 Schema；这是保守预算，不冒充供应商实际 Token 用量。
    def content_size(content):
        if isinstance(content,list):
            # 图片是独立模态输入，Base64 传输体积不是文本 Token；为每张图片单独预留预算。
            return sum(4096 if isinstance(part,dict) and part.get('type')=='image_url' else size(part) for part in content)
        return size(content)
    return sum(content_size(getattr(m, 'content', m)) + 32 +
        size(getattr(m, 'additional_kwargs', {}).get('reasoning_content', '')) +
        (size(m.tool_calls) if getattr(m, 'tool_calls', None) else 0) +
        (size(m.tool_call_id) if getattr(m, 'tool_call_id', None) else 0)
        for m in messages) + size(extra or '') + 256


def check_request(profile, messages, extra=None, *, output_tokens=None):
    from .context_meter import active_meter
    meter = active_meter.get()
    amount = meter.measure(profile, messages, extra) if meter else request_size(messages, extra)
    if amount > input_limit(profile, output_tokens=output_tokens):
        raise ContextOverflow('请求超过当前上下文预算，需要继续分段整理。')
    return amount


def material_limit(profile, budget, overhead=0):
    """资料占窗口的 10%；运行预算收缩时同比降低，并为完整请求留安全余量。"""
    token = active_budget.set(None)
    try:
        initial = input_limit(profile)
    finally:
        active_budget.reset(token)
    target = int((profile.get('context_window') or 32768) * 0.1 * min(1, budget / initial))
    available = min(target, budget - overhead - 512)
    if available < 2:
        raise ContextOverflow('当前请求没有足够空间读取资料，需要先整理背景。')
    return available


def message_payload(message, timezone_offset=0):
    """模型只接收必要来源信息；附件原始结构保存在本地，不重复计入文本。"""
    item = {key: message.get(key) for key in ('source', 'username', 'time', 'sender', 'text')}
    # 时间戳保留作定位；模型直接读取程序换算的带时区时间，禁止自行做 Unix 换算。
    # 默认 UTC 与任务时区采用同长度格式，读取预估与正式请求计入相同字段。
    try:
        item['sent_at'] = datetime.fromtimestamp(message['time'], timezone(timedelta(seconds=timezone_offset or 0))).isoformat(timespec='seconds')
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        item['sent_at'] = None
    # 会话显示名与发送者分别传递；压缩后的来源元信息也复用此结构。
    if message.get('name'):
        item['name'] = message['name']
    if message.get('sender_id') and message['sender_id'] != message.get('sender'):
        item['sender_id'] = message['sender_id']
    if message.get('sender_aliases'):
        item['sender_aliases'] = message['sender_aliases']
    for key in ('text_offset', 'next_text_offset', 'fragment_complete', 'context_only'):
        if message.get(key):
            item[key] = message[key]
    return item


def is_context_error(exc):
    text = str(exc).lower()
    return any(x in text for x in ('context_length_exceeded', 'maximum context', 'context window',
        'prompt is too long', 'input is too long', 'too many input tokens', 'request too large'))


def pieces(text, limit):
    """不破坏 Unicode 字符边界，分段后可完整拼回原文。"""
    current, amount = [], 0
    for char in text:
        weight = len(char.encode('utf-8'))
        if current and amount + weight > limit:
            yield ''.join(current)
            current, amount = [], 0
        current.append(char)
        amount += weight
    if current:
        yield ''.join(current)
