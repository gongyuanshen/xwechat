"""将目录能力转换为可选档位与已确认的请求格式，不按模型名称猜档位。"""
from urllib.parse import urlparse


def options(value):
    result = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        kind = item.get('type')
        if kind == 'effort':
            values = item.get('values')
            if isinstance(values, list) and values and all(isinstance(v, str) and 0 < len(v) <= 40 for v in values):
                result.append({'type': kind, 'values': list(dict.fromkeys(values))[:20]})
        elif kind == 'toggle':
            result.append({'type': kind})
        elif kind == 'budget_tokens':
            bounds = {k: item[k] for k in ('min', 'max') if type(item.get(k)) is int and 0 <= item[k] <= 10000000}
            if 'min' in bounds and bounds.get('max', bounds['min']) >= bounds['min']:
                result.append({'type': kind, **bounds})
    return result


def dialect(profile):
    if profile.get('protocol') == 'anthropic':
        return 'anthropic'
    host = urlparse(profile.get('base_url', '')).hostname or ''
    if host in ('api.deepseek.com', 'api.xiaomimimo.com', 'api.moonshot.cn', 'api.moonshot.ai', 'open.bigmodel.cn', 'api.z.ai', 'ark.cn-beijing.volces.com'):
        return 'thinking'
    if host in ('dashscope.aliyuncs.com', 'dashscope-intl.aliyuncs.com', 'dashscope-us.aliyuncs.com', 'api.siliconflow.cn', 'api.siliconflow.com'):
        return 'enable_thinking'
    if host == 'generativelanguage.googleapis.com':
        return 'google'
    if host == 'openrouter.ai':
        return 'openrouter'
    return 'openai'


def controls(profile, metadata):
    declared = options(metadata.get('reasoning_options'))
    # 手动/上游原生等级仍可覆盖目录；空列表是明确取消，不能又从目录补回。
    efforts = metadata.get('reasoning_efforts')
    if not isinstance(efforts, list):
        efforts = next((item['values'] for item in declared if item['type'] == 'effort'), [])
    efforts = list(dict.fromkeys(v for v in efforts if isinstance(v, str) and 0 < len(v) <= 40))[:20]
    wire = dialect(profile)
    toggle = any(item['type'] == 'toggle' for item in declared) or set(metadata.get('thinking_types') or []) >= {'enabled', 'disabled'}
    budget = next((item for item in declared if item['type'] == 'budget_tokens'), None)
    bounded = None
    if budget and wire in ('anthropic', 'google', 'openrouter', 'enable_thinking'):
        # 当前 Agent 每步输出预留为 8192；思考预算必须留出回答空间。
        from .agent_budget import model_output_limit
        current = {**profile, 'model_metadata': metadata}
        if type(metadata.get('limit', {}).get('context')) is int:
            current['context_window'] = metadata['limit']['context']
        ceiling = min(budget.get('max', 8191), model_output_limit(current) - 1)
        if budget['min'] <= ceiling:
            bounded = {'min': budget['min'], 'max': ceiling}
    # Anthropic 开启思考需要预算或 adaptive 模式，不把未知模型硬套为 adaptive。
    can_toggle = toggle and wire in ('thinking', 'enable_thinking', 'openrouter')
    return {'efforts': efforts, 'toggle': can_toggle, 'budget': bounded,
            'declared': bool(declared or efforts or toggle),
            'adjustable': bool(efforts or can_toggle or bounded)}


def validate(profile, effort=None, mode=None, budget=None):
    from .providers import ProviderFailure
    capability = controls(profile, profile.get('model_metadata') or {})
    if effort is not None and effort not in capability['efforts']:
        raise ProviderFailure('当前模型未声明此原生思考等级，请刷新模型能力后重试。')
    if mode is not None and (mode not in ('enabled', 'disabled') or not capability['toggle']):
        raise ProviderFailure('当前服务未确认支持此思考开关，请使用模型默认设置。')
    if budget is not None and (type(budget) is not int or not capability['budget'] or
                              not capability['budget']['min'] <= budget <= capability['budget']['max']):
        raise ProviderFailure('思考预算不在当前模型和本轮输出容量支持的范围内。')
    if (mode == 'disabled' and (effort is not None or budget is not None)) or (effort is not None and budget is not None):
        raise ProviderFailure('请在关闭思考、原生档位和思考预算中选择一种设置。')
    return {**profile, 'reasoning_effort': effort, 'thinking_mode': mode, 'thinking_budget': budget}


def explicit_reasoning(profile):
    """The user picked a thinking mode, effort or budget; automatic thinking limits must not override it."""
    return any(profile.get(key) is not None for key in ('reasoning_effort', 'thinking_mode', 'thinking_budget'))


def constrain_reasoning_profile(profile, metadata=None):
    """在事实提取阶段收敛思考深度：
    - 若模型方言支持显式关闭思考，直接关闭思考；
    - 若模型支持原生推理等级，将 reasoning_effort 设为 'low'；
    - 若模型支持思考预算，将 thinking_budget 钳制为 min(budget or 1024, 1024)。
    """
    from .model_catalog import documented_metadata
    meta = metadata if metadata is not None else (profile.get('model_metadata') or documented_metadata(profile, profile.get('model', '')))
    capability = controls(profile, meta)
    wire = dialect(profile)

    if wire in ('thinking', 'enable_thinking') or (wire == 'openrouter' and capability.get('toggle')):
        return {**profile, 'thinking_mode': 'disabled', 'reasoning_effort': None, 'thinking_budget': None}

    if 'low' in capability.get('efforts', []) or profile.get('reasoning_effort') is not None:
        return {**profile, 'reasoning_effort': 'low', 'thinking_mode': None, 'thinking_budget': None}

    budget = profile.get('thinking_budget')
    if budget is not None or capability.get('budget'):
        target_budget = min(budget or 1024, 1024)
        if capability.get('budget') and 'min' in capability['budget']:
            target_budget = max(capability['budget']['min'], target_budget)
        return {**profile, 'thinking_budget': target_budget, 'thinking_mode': 'enabled', 'reasoning_effort': None}

    return profile


def request_options(profile):
    """返回客户端参数；供应商扩展统一放入 extra_body，恢复默认时不发覆盖项。"""
    from .model_execution import call_policy
    if call_policy.get().constrain_thinking:
        profile = constrain_reasoning_profile(profile)
    effort, mode, budget = (profile.get(key) for key in ('reasoning_effort', 'thinking_mode', 'thinking_budget'))
    wire = dialect(profile)
    if wire == 'anthropic':
        extra = {'output_config': {'effort': effort}} if effort is not None else {}
        if budget is not None:
            extra['thinking'] = {'type': 'enabled', 'budget_tokens': budget}
        return extra
    result = {'reasoning_effort': effort} if effort is not None else {}
    body = {}
    if wire == 'openrouter':
        reasoning = {}
        if effort is not None:
            result = {}; reasoning['effort'] = effort
        if mode is not None:
            reasoning['enabled'] = mode == 'enabled'
        if budget is not None:
            reasoning['max_tokens'] = budget
        if reasoning:
            body['reasoning'] = reasoning
    elif wire == 'thinking':
        if mode is not None or effort is not None:
            body['thinking'] = {'type': mode or 'enabled'}
    elif wire == 'enable_thinking':
        if mode is not None or budget is not None or effort is not None:
            body['enable_thinking'] = mode != 'disabled'
        if budget is not None:
            body['thinking_budget'] = budget
    elif wire == 'google' and budget is not None:
        body['google'] = {'thinking_config': {'thinking_budget': budget}}
    if body:
        result['extra_body'] = body
    return result
