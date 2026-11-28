"""独立批次事实的无损汇集；模型不再反复改写已提交的历史笔记。"""
import json
import re
from datetime import datetime, timezone, timedelta


def combine_notes(notes):
    items, seen, uncertainties = [], set(), []
    for note in notes:
        for item in note.get('items', []):
            fingerprint = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if fingerprint not in seen:
                seen.add(fingerprint)
                items.append(item)
        uncertainties.extend(note.get('uncertainties', []))
    return {'overview': '以下为各批次保存的事实；按来源核对事件先后并合并同一活动的变化。',
            'items': items, 'uncertainties': list(dict.fromkeys(uncertainties))}


def normalize_date_headings(text, default_year=None):
    """标题已明确给出年月日时，星期由程序计算；不改正文中的原文引述或活动日期。"""
    def replace(match):
        try:
            year = int(match[2]) if match[2] else default_year
            if year is None:
                return match.group()
            weekday = datetime(year, int(match[3]), int(match[4])).weekday()
        except ValueError:
            return match.group()
        return match[1] + '星期' + '一二三四五六日'[weekday] + match[5]
    return re.sub(r'^(#{1,6}\s+(?:\*\*)?(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日\s*[（(])(?:星期|周)[一二三四五六日天]([）)])',
                  replace, text, flags=re.MULTILINE)


def answer_year(run):
    """查询范围完全落在同一年时，才为省略年份的标题提供确定年份。"""
    interval = run.get('time_range') or {}
    if not interval.get('end') or interval.get('start') is None:
        return None
    zone = timezone(timedelta(seconds=run.get('timezone_offset', 0)))
    first = datetime.fromtimestamp(interval['start'], zone).year
    last = datetime.fromtimestamp(interval['end'] - 1, zone).year
    return first if first == last else None


def saved_notes(workspace, run):
    """只沿已提交根节点取笔记，避免混入未提交结果、旧版本或不兼容的范围。"""
    if not run.get('note_key'):
        return {}
    with workspace.store.connection() as db:
        rows = db.execute("SELECT id,body FROM agent_piece WHERE run_id=? AND version=? AND kind='stage_note'",
                          (run['id'], run['version'])).fetchall()
    by_id = {key: json.loads(body) for key, body in rows}
    chain, visited = [], set()
    key = run.get('note_key')
    while key and key not in visited:
        visited.add(key)
        note = by_id.get(key)
        if note is None:
            raise ValueError('已提交笔记链缺失，不能跳过历史事实。')
        chain.append(note['notes'])
        # 兼容旧累计笔记及跨任务继承的完整摘要，它们自身已经代表此前历史。
        if note.get('note_strategy') != 'incremental':
            break
        key = note.get('parent')
    return combine_notes(reversed(chain))


SAFE_NOTE_WINDOW_BYTES: int = 48 * 1024  # 48 KiB safe input window for aggregated notes
RELAY_CHUNK_MAX_BYTES: int = 16 * 1024   # 16 KiB per relay compression chunk

REPORT_SYNTHESIZER_SYSTEM_PROMPT = """你是一位资深微信群聊数据分析与长报告统稿架构师。
你的任务是将多批次切片提炼出的结构化事实笔记，统揽全局，撰写为一份结构严谨、逻辑清晰、全周期覆盖的全局长期治理分析报告。

【核心纪律与准则】
1. 真实来源与零幻觉：
   - 报告中的每一项具体事实、数据或结论，必须在句末准确标注真实消息来源编号，格式为 [[24位source_id]]。严禁编造任何不存在的来源编号。
2. 引号与逐字引文极度严格（防止引文核验失败）：
   - 转述、概括与归纳结论【绝对禁止使用任何双引号（“”或""）】！直接用通顺客观的书面语表述。
   - 严禁滥用引号！仅在需要 100% 逐字引用消息原文原话时，才允许使用中文双引号“”，且引号内的字符必须与对应消息原文 100% 逐字逐字符完全一致，严禁任何改写、拼接、错字或同义词替换。若无法确保逐字完全一致，必须去掉引号改用纯文字转述。
3. 公历日历与星期一致性：
   - 所有每日进程的三级标题必须包含确切年月日与星期，格式统一为：### YYYY年MM月DD日（星期X） 或 ### YYYY年MM月DD日（周X）。
   - 必须严格遵循格里高利公历换算实际星期，系统将进行确定性公历校验，星期错误将被直接阻断。
4. 全周期与讨论主线完整覆盖：
   - 报告必须覆盖整个分析周期的首日至末日，忠实反映讨论与事件的演变过程。

【报告标准四级章节结构】
你输出的长报告必须采用标准 Markdown 格式，严格按照以下四级大纲组织：

# 微信群聊长期治理分析报告

## 一、执行周期与全局概览
- 交代本次分析涵盖的总消息条数、批次切片数、日历天数跨度（从 YYYY-MM-DD 至 YYYY-MM-DD）。
- 概述整个分析周期内的核心结论、关键决策演进与总体态势。

## 二、专题主线与业务板块梳理
- 按讨论主题（如：研发架构治理、商务协同、应急事件响应、日常协调等）分板块梳理，交代每个维度的总体发展脉络与关键里程碑。
- 每个专题结论紧随支持的真实来源 [[source_id]]。

## 三、逐日进程与事件时间线
- 按照时间先后顺序展开，每一天采用三级标题：
  ### YYYY年MM月DD日（星期X）
- 在每日标题下，平铺梳理当天发生的关键事实、决策或讨论演变，详细标注发言人、时间点与对应来源 [[source_id]]。
- 若某日无特定业务活动或仅为日常寒暄，亦应简要说明，不得随意跳过或遗漏日历天。

## 四、关键待办、疑点与风险提示
- 归纳总结尚未解决的分歧、未落实的待办事项、带有 uncertainty/conflicting 标记的事实。
- 提出后续需要进一步核实的数据或行动建议。
"""


class StageNoteCorruptedError(ValueError):
    """持久化笔记损坏；定位信息不包含聊天正文或字段值。"""
    def __init__(self, piece_id, run_id, version, reason):
        super().__init__(f'Corrupted stage note in agent_piece: {piece_id} '
                         f'[run_id={run_id}, version={version}]: {reason}')


def stage_note_payload(body_raw, piece_id, run_id, version):
    """解析既有裸字典与虚拟文件包装，损坏记录不得参与恢复或统计。"""
    try:
        wrapper = json.loads(body_raw) if isinstance(body_raw, str) else body_raw
    except json.JSONDecodeError as exc:
        raise StageNoteCorruptedError(piece_id, run_id, version, 'invalid wrapper JSON') from exc
    if not isinstance(wrapper, dict):
        raise StageNoteCorruptedError(piece_id, run_id, version, 'wrapper must be an object')
    content = wrapper['content'] if 'content' in wrapper else wrapper
    try:
        note = json.loads(content) if isinstance(content, str) else content
    except json.JSONDecodeError as exc:
        raise StageNoteCorruptedError(piece_id, run_id, version, 'invalid content JSON') from exc
    if not isinstance(note, dict):
        raise StageNoteCorruptedError(piece_id, run_id, version, 'content must be an object')
    return note


def load_stage_notes(store, run_id: str, version: int = 1) -> list[dict]:
    """从 SQLite agent_piece 中按顺序读取所有 /notes/batch_*.json。"""
    conn_ctx = store.connection() if hasattr(store, 'connection') else store.store.connection()
    with conn_ctx as db:
        rows = db.execute(
            "SELECT id, body FROM agent_piece "
            "WHERE run_id=? AND version=? AND kind='deep_file' AND id GLOB 'file:/notes/batch_*.json' "
            "ORDER BY id",
            (run_id, version)
        ).fetchall()
    notes = []
    for piece_id, body_raw in rows:
        note = stage_note_payload(body_raw, piece_id, run_id, version)
        for field, minimum in (('batch_index', 1), ('messages_count', 0)):
            if type(note.get(field)) is not int or note[field] < minimum:
                raise StageNoteCorruptedError(piece_id, run_id, version, f'{field} must be an integer >= {minimum}')
        for field in ('sources', 'facts'):
            if not isinstance(note.get(field), list):
                raise StageNoteCorruptedError(piece_id, run_id, version, f'{field} must be a list')
        if any(not isinstance(source, str) for source in note['sources']):
            raise StageNoteCorruptedError(piece_id, run_id, version, 'sources must contain strings')
        for field in ('scope_handle', 'cursor', 'committed_at'):
            if field in note and not isinstance(note[field], str):
                raise StageNoteCorruptedError(piece_id, run_id, version, f'{field} must be a string')
        note['file_path'] = piece_id.removeprefix('file:')
        notes.append(note)
    # 不同范围各自从第一批开始，最新游标按实际提交时间确定。
    notes.sort(key=lambda n: (n.get('committed_at', ''), n['batch_index'], n['file_path']))
    return notes


def load_legacy_stage_notes(store, run_id, version):
    """读取已知的旧版扁平发现或累计笔记格式，不将损坏记录投影为零事实。"""
    with store.connection() as db:
        rows = db.execute("SELECT id,body FROM agent_piece WHERE run_id=? AND version=? "
                          "AND kind='stage_note' ORDER BY id", (run_id, version)).fetchall()
    notes = []
    for index, (piece_id, body_raw) in enumerate(rows, 1):
        body = stage_note_payload(body_raw, piece_id, run_id, version)
        if 'items' in body:
            facts = body['items']
        elif isinstance(body.get('notes'), dict):
            facts = body['notes'].get('items')
        else:
            raise StageNoteCorruptedError(piece_id, run_id, version, 'missing items or notes.items')
        covered = body.get('covered')
        if not isinstance(facts, list) or any(not isinstance(fact, dict) for fact in facts):
            raise StageNoteCorruptedError(piece_id, run_id, version, 'items must be a list of objects')
        if not isinstance(covered, list) or any(not isinstance(item, dict) or not isinstance(item.get('source'), str) for item in covered):
            raise StageNoteCorruptedError(piece_id, run_id, version, 'covered must contain objects with string sources')
        cursor = body.get('cursor', '')
        if not isinstance(cursor, str):
            raise StageNoteCorruptedError(piece_id, run_id, version, 'cursor must be a string')
        notes.append({'file_path': piece_id, 'batch_index': index, 'cursor': cursor,
                      'messages_count': len(covered), 'facts': facts,
                      'sources': [item['source'] for item in covered]})
    return notes


def check_notes_exceed_safe_window(notes: list[dict], safe_window_bytes: int = SAFE_NOTE_WINDOW_BYTES) -> tuple[bool, int]:
    """检测当前已保存笔记的总大小是否超出安全窗口（默认 48 KiB）。"""
    total_bytes = sum(len(json.dumps(n, ensure_ascii=False).encode('utf-8')) for n in notes)
    return total_bytes > safe_window_bytes, total_bytes


def partition_notes_for_relay(notes: list[dict], max_chunk_bytes: int = RELAY_CHUNK_MAX_BYTES) -> list[list[dict]]:
    """将连续批次笔记切分为不超过 max_chunk_bytes（默认 16 KiB）的接力分组。"""
    groups, current_group = [], []
    current_size = 0
    for note in notes:
        note_size = len(json.dumps(note, ensure_ascii=False).encode('utf-8'))
        if current_group and (current_size + note_size > max_chunk_bytes):
            groups.append(current_group)
            current_group, current_size = [], 0
        current_group.append(note)
        current_size += note_size
    if current_group:
        groups.append(current_group)
    return groups


def deduplicate_facts(facts: list) -> list:
    """去重具有相同来源集、原话引用及归一化文本的事实记录。"""
    seen = set()
    deduped = []
    for f in facts:
        if isinstance(f, dict):
            src_tuple = tuple(sorted(f.get('sources', [])))
            fp = (src_tuple, f.get('quote') or '', re.sub(r'\s+', '', str(f.get('text', ''))))
            if fp not in seen:
                seen.add(fp)
                deduped.append(f)
        elif isinstance(f, str):
            fp = re.sub(r'\s+', '', f)
            if fp not in seen:
                seen.add(fp)
                deduped.append(f)
        else:
            deduped.append(f)
    return deduped


def consolidate_aggregated_note(notes: list[dict], is_relay: bool = False) -> dict:
    """将多个批次笔记或接力笔记整合为统一的 /notes/aggregated.json 结构。"""
    all_facts = []
    merged_mappings = {}
    all_sources = set()
    total_messages = 0
    unresolved = []

    for n in notes:
        total_messages += n.get('messages_count', len(n.get('sources', [])))
        all_sources.update(n.get('sources', []))
        merged_mappings.update(n.get('message_mappings', {}))
        facts = n.get('facts') or n.get('consolidated_facts') or []
        all_facts.extend(facts)
        unresolved.extend(n.get('unresolved', []))

    deduped_facts = deduplicate_facts(all_facts)
    cited_sources = set()
    for f in deduped_facts:
        if isinstance(f, dict):
            cited_sources.update(f.get('sources', []))
        elif isinstance(f, str):
            cited_sources.update(re.findall(r'\[\[([a-f0-9]{24}|src_[^\]]+)\]\]', f))

    compact_mappings = {s: merged_mappings[s] for s in cited_sources if s in merged_mappings} if cited_sources else merged_mappings

    return {
        'total_batches': len(notes),
        'total_messages': total_messages,
        'total_facts': len(deduped_facts),
        'relay_compressed': is_relay,
        'sources': sorted(all_sources),
        'message_mappings': compact_mappings,
        'facts': deduped_facts,
        'unresolved': list(dict.fromkeys(unresolved)),
        'aggregated_at': datetime.now(timezone.utc).isoformat(),
    }


def compose_synthesis_context(notes: list[dict], run: dict, originals: dict) -> dict:
    """组装全周期统稿上下文：时间跨度、公历每日进程、专题主线、关键实体及原话映射预览。"""
    tz_offset = run.get('timezone_offset', 28800)
    zone = timezone(timedelta(seconds=tz_offset))

    all_facts = []
    source_mappings = {}
    total_messages = 0
    unresolved_items = []

    for note in notes:
        total_messages += note.get('messages_count', len(note.get('sources', [])))
        source_mappings.update(note.get('message_mappings', {}))
        facts = note.get('facts') or note.get('consolidated_facts') or []
        for fact in facts:
            all_facts.append(fact)
        unresolved_items.extend(note.get('unresolved', []))

    for s, orig in originals.items():
        if s not in source_mappings and isinstance(orig, dict):
            source_mappings[s] = {
                'sender': orig.get('sender') or orig.get('username') or '',
                'sent_at': orig.get('sent_at') or '',
                'time': orig.get('time', 0),
                'text': orig.get('text', '')[:120],
            }

    def get_fact_time(f):
        if isinstance(f, dict):
            for s in f.get('sources', []):
                if s in source_mappings and source_mappings[s].get('time'):
                    return source_mappings[s]['time']
                if s in originals and isinstance(originals.get(s), dict) and originals[s].get('time'):
                    return originals[s]['time']
        return 0

    all_facts.sort(key=get_fact_time)

    daily_groups = {}
    thematic_groups = {}
    key_entities = set()

    for fact in all_facts:
        f_time = get_fact_time(fact)
        if f_time > 0:
            day_str = datetime.fromtimestamp(f_time, zone).strftime('%Y-%m-%d')
        else:
            day_str = '未确定日期'

        daily_groups.setdefault(day_str, []).append(fact)

        if isinstance(fact, dict):
            relation = fact.get('relation') or '综合讨论'
            thematic_groups.setdefault(relation, []).append(fact)

            for ent in fact.get('entities', []):
                key_entities.add(ent)
            sender = fact.get('sender')
            if sender:
                key_entities.add(sender)

            if fact.get('evidence_status') in ('uncertain', 'conflicting'):
                unresolved_items.append(fact.get('text', str(fact)))
        else:
            thematic_groups.setdefault('综合讨论', []).append(fact)

    timeline_days = []
    for day_str in sorted(d for d in daily_groups if d != '未确定日期'):
        dt = datetime.strptime(day_str, '%Y-%m-%d')
        weekday_str = '星期' + '一二三四五六日'[dt.weekday()]
        timeline_days.append({
            'date': day_str,
            'weekday': weekday_str,
            'facts_count': len(daily_groups[day_str]),
            'facts': daily_groups[day_str],
        })

    if '未确定日期' in daily_groups:
        timeline_days.append({
            'date': '未确定日期',
            'weekday': '无',
            'facts_count': len(daily_groups['未确定日期']),
            'facts': daily_groups['未确定日期'],
        })

    valid_times = [m.get('time', 0) for m in source_mappings.values() if isinstance(m, dict) and m.get('time')]
    start_ts = min(valid_times, default=0)
    end_ts = max(valid_times, default=0)
    days_count = max(1, (end_ts - start_ts) // 86400 + 1) if end_ts >= start_ts and start_ts > 0 else 1

    compact_sources = {}
    for s, m in source_mappings.items():
        if isinstance(m, dict):
            compact_sources[s] = {
                'sender': m.get('sender', ''),
                'sent_at': m.get('sent_at', ''),
                'text': m.get('text', '')[:120],
            }

    return {
        'total_messages': total_messages,
        'total_batches': len(notes),
        'time_span': {
            'start': start_ts,
            'end': end_ts,
            'start_iso': datetime.fromtimestamp(start_ts, zone).isoformat() if start_ts else '',
            'end_iso': datetime.fromtimestamp(end_ts, zone).isoformat() if end_ts else '',
            'days': days_count,
        },
        'conversations': run.get('query_scope', []),
        'key_entities': sorted(key_entities),
        'timeline_days': timeline_days,
        'thematic_clusters': thematic_groups,
        'unresolved_items': list(dict.fromkeys(unresolved_items)),
        'source_mappings': compact_sources,
    }
