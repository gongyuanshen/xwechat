"""只核验可由程序确定的正文错误；语义是否被引文支持仍须另行查证。"""
import re
from datetime import datetime, timedelta, timezone


def requires_findings(text):
    """完整内容分析与精确计数分别约束，防止统计冒充分析。"""
    if re.search(r'(?:不需要|不要|无需|不用).{0,8}(?:读取|查询|搜索).{0,4}(?:聊天|数据|消息)', text):
        return False
    if re.search(r'(?:不需要|不要|无需|不用).{0,6}(?:完整|全面|全量|全历史|逐笔|从头到尾)', text):
        return False
    return bool(re.search(r'(?:全量|全历史|从头到尾|不遗漏|逐笔).{0,20}(?:梳理|核对|核账|读取|总结|分析|提取|借还款|时间线)'
        r'|(?:梳理|核对|核账|读取|总结|分析|提取).{0,20}(?:全量|全历史|从头到尾|不遗漏|逐笔)'
        r'|(?:完整|全面).{0,16}(?:总结|报告|分析|读取)|(?:总结|报告|分析).{0,16}(?:完整|全面)'
        r'|(?:全部|所有|逐条).{0,12}提取|提取.{0,12}(?:全部|所有|逐条)', text))


def requires_complete_analysis(text):
    """识别明确的覆盖约束，不增加模型路由或替代正常工具选择。"""
    if re.search(r'(?:不需要|不要|无需|不用).{0,8}(?:读取|查询|搜索).{0,4}(?:聊天|数据|消息)', text):
        return False
    if requires_findings(text):
        return True
    if re.search(r'(?:不需要|不要|无需|不用).{0,6}(?:完整|全面|全量|全历史|逐笔|从头到尾)', text):
        return False
    if re.search(r'精确统计', text) and re.search(r'欠|金额|借还款|借款|费用', text) and not re.search(r'消息|条数|频率', text):
        return False
    return bool(re.search(r'(?:完整|全面).{0,16}(?:总结|报告|分析|读取)|(?:总结|报告|分析).{0,16}(?:完整|全面)'
        r'|(?:精确统计|统计全部|统计所有|全部消息.{0,6}统计)'
        r'|(?:全部|所有|逐条).{0,12}提取|提取.{0,12}(?:全部|所有|逐条)', text))


def calendar_issues(text, run):
    if not isinstance(run, dict):
        raise ValueError('run missing cutoff or time_range for calendar validation')
    zone = timezone(timedelta(seconds=run.get('timezone_offset', 28800)))
    cutoff = run.get('cutoff')
    if cutoff is None:
        tr = run.get('time_range') or {}
        cutoff = tr.get('end')
    if cutoff is None:
        evidence = run.get('evidence') or run.get('originals') or run.get('sources') or run.get('source_mappings')
        candidate_times = []
        if isinstance(evidence, dict):
            for v in evidence.values():
                if isinstance(v, dict) and v.get('time'):
                    candidate_times.append(v['time'])
        elif isinstance(evidence, list):
            for v in evidence:
                if isinstance(v, dict) and v.get('time'):
                    candidate_times.append(v['time'])
        if candidate_times:
            cutoff = max(candidate_times)
    if cutoff is None:
        raise ValueError('run missing cutoff or time_range for calendar validation')
    year = datetime.fromtimestamp(cutoff, zone).year
    issues = []
    # 仅检查显式绑定在一起的月日和星期，不把聊天中的“周三”强配到消息日期。
    pattern = r'(?:(\d{4})年)?(\d{1,2})(?:月|/)(\d{1,2})日?\s*[（(]\s*(?:周|星期)([一二三四五六日天])\s*[）)]'
    for match in re.finditer(pattern, text):
        try:
            date = datetime(int(match[1] or year), int(match[2]), int(match[3]))
        except ValueError:
            issues.append('无效日期：' + match[0])
            continue
        expected = '一二三四五六日'[date.weekday()]
        if match[4].replace('天', '日') != expected:
            issues.append(f'日期与星期不一致：{match[0]}，该公历日期是周{expected}。请核对原文中的活动日期与消息发送日期；不能仅改星期把错误活动日期保留下来。不能确定活动日期时保留原文相对日期并注明消息时间。')
    return issues


def temporal_issues(text, originals, timezone_offset=0):
    """拦截常见的通知时间升级；这里只提出核查问题，不推断实际事件时间。"""
    issues = []
    timing = r'按时(?!间)|按期|准时|(?:提前|延迟|晚了|晚约)\s*\d+\s*(?:分钟|小时)|(?:在|于)(?:该|这个|这一|上述)时间点.{0,8}(?:完成|发布|上线|结束)'
    # 直接引文中的“已完成”属于发言内容；不能与引文外的发送时刻拼成事件断言。
    narrative = re.sub(r'“[^”]*”|"[^"\n]*"|「[^」]*」', '', text)
    for sentence in re.split(r'[。！？\n]', narrative):
        # 摘要中的状态升级不能由准备动作证明；事件名称来自正文，不限定具体业务主题。
        for event in re.findall(r'(?:^|[，：；])\s*([\u4e00-\u9fff]{2,6})(?:已成事实|已经生效)', sentence):
            mentions = [m.get('text', '') for m in originals.values() if event in m.get('text', '')]
            preparations = r'(?:提|申请|计划|准备|打算|考虑|建议|希望|等待|尚未|前提).{0,8}' + re.escape(event)
            completed = r'(?<!提)(?<!请)(?<!未)' + re.escape(event) + r'(?:已经|已|了|完成|生效)'
            if mentions and any(re.search(preparations, s) for s in mentions) and not any(re.search(completed, s) and not re.search(preparations, s) for s in mentions):
                issues.append('结果状态可能被升级：' + sentence + '。现有原文仅支持申请、意向或条件，不能表述该结果已成事实或生效；按原文分别说明已发生的动作和未完成的事项。')
        clocks = re.findall(r'(?<!\d)(\d{1,2}[:：]\d{2})(?!\d)', sentence)
        if clocks and not sentence.lstrip().startswith('|') and re.search(r'完成|成功|实际|已于', sentence) and not re.search(r'消息|通知|报告|发言|原话|依据|说|称|计划|预计|建议|无法|不能|不等于', sentence):
            for clock in clocks:
                local_clock = f'{int(clock[:-3]):02d}:' + clock[-2:]
                clock_sources = [m.get('text', '') for m in originals.values() if clock.replace('：', ':') in m.get('text', '').replace('：', ':')]
                completed_at_clock = any(re.search(r'已完成|已发布|发布完成|完成发布|已上线|已结束|结清|完成了|成功', value)
                    and not re.search(r'尚未|计划|预计|将于|仍按', value) for value in clock_sources)
                if clock_sources and not completed_at_clock and re.search(r'最终|实际|已经|成功|完成了', sentence):
                    issues.append('计划时刻不能证明完成时刻：' + sentence + '。原文中的该时刻只用于安排或确认计划，未明确实际在该时刻完成；分开说明计划时间与后来报告完成的状态。')
                    break
                matched = [m for m in originals.values() if datetime.fromtimestamp(m.get('time', 0), timezone(timedelta(seconds=timezone_offset))).strftime('%H:%M') == local_clock]
                # 要求至少有同时刻通知作为误用嫌疑；原文明示时刻时仍交给语义核验。
                if matched and not any(clock.replace('：', ':') in m.get('text', '').replace('：', ':') for m in originals.values()):
                    issues.append('事件时刻可能误用了通知时间：' + sentence + '。原文未明示该精确事件时刻；应说明此时有人报告已完成，不能把发送时刻写成实际完成时刻。')
                    break
        if not re.search(timing, sentence) or re.search(r'无法|不能|不代表|不等于|未能|尚不|是否', sentence):
            continue
        explicit = [m for m in originals.values() if re.search(timing, m.get('text', ''))
            and not re.search(r'计划|建议|预计|尚未|打算|准备|仍按|将于', m.get('text', ''))]
        dated_completion = [m for m in originals.values() if re.search(r'\d{1,2}[:：]\d{2}', m.get('text', ''))
            and re.search(r'已完成|已发布|发布完成|已上线|已结束|结清', m.get('text', ''))
            and not re.search(r'计划|建议|预计|尚未|打算|准备|将于', m.get('text', ''))]
        if not explicit and not dated_completion:
            issues.append('精确履约时间缺少原文依据：' + sentence + '。消息发送时间不能证明事件发生时间或是否按期；保留已报告完成的状态，删除无依据的准时、提前或延迟判断。')
    return issues
