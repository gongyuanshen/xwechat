"""Controlled, reproducible benchmark of the real unified-search SQLite paths.

Only synthetic records are created. No existing account, model or OCR is opened.
Example: .venv/Scripts/python tools/benchmark_unified_search.py --output .work/unified-search-20261013/before
Rerun with --baseline <before>/summary.json to assert result identity after changes.
Timings exclude fixture creation, HTTP, desktop rendering, index readiness and OS
cache eviction. Each scenario has one unmeasured warm-up and repeated real reads.
"""
import argparse
from contextlib import ExitStack, closing
import cProfile
import hashlib
import json
import math
import os
from pathlib import Path
import pstats
import sqlite3
import sys
import time
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
ACCOUNT = 'synthetic-unified-benchmark'
SOURCES = ('chat', 'documents', 'ocr', 'notes')
EPOCH = 1_700_000_000
FIXTURE_VERSION = 2


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def distribution(values):
    ordered = sorted(values)
    return {'samples': len(values), 'p50_ms': round(ordered[math.ceil(len(values) * .5) - 1], 3),
            'p95_ms': round(ordered[math.ceil(len(values) * .95) - 1], 3)}


def corpus(number, size, source):
    parts = [f'项目进度 第{number}份生产记录。']
    if number % 5 == 0:
        parts.append('电池回收 拆解工序已排程。')
    if number % 7 == 0:
        parts.append('MilestoneREADY 已确认。')
    if number % 11 == 0:
        parts.append('订单ZX-42 金额10.50。')
    elif number % 11 == 1:
        parts.append('订单ZX 42 金额10x50。')
    if number % 13 == 0:
        parts.append('预算审批。')
    if number == size // 2:
        parts.append('稀有凭据R-ONLY。')
    if number % 17 == 0:
        parts.append('签批👍。')
    # Attachment caches carry a longer body than chat previews or library notes.
    padding = {'chat': 1, 'documents': 24, 'ocr': 12, 'notes': 6}[source]
    parts.append('设备状态正常，值班记录已归档。' * padding)
    notes = '周五提交。' if number % 13 == 0 else '待复核。'
    if source != 'notes':
        parts.append(notes)
        notes = ''
    return ''.join(parts), notes


def identity(source, number, size):
    return f'library-{number:06}' if source == 'notes' else f'message_0:Msg_bench:{SOURCES.index(source) * size + number}'


def seed(folder, size):
    from wechat_decrypt_tool.ai.agent_tools import attachment_message_signature, normalize
    from wechat_decrypt_tool.ai.storage import AIStore
    from wechat_decrypt_tool.attachment_service import attachment_key
    from wechat_decrypt_tool.chat_helpers import _to_char_token_text
    from wechat_decrypt_tool.chat_search_index import _init_index_db, _create_message_meta_indexes
    from wechat_decrypt_tool.library_service import LibraryService

    folder.mkdir(parents=True, exist_ok=False)
    path = folder / 'search.sqlite3'
    store = AIStore(folder / 'ai')
    with closing(sqlite3.connect(path)) as db, db, store.connection() as records:
        _init_index_db(db)
        LibraryService.put(records, 'library_folder', {'id': 'folder', 'account': ACCOUNT,
            'name': '合成研究资料', 'created_at': EPOCH, 'updated_at': EPOCH})
        for source in SOURCES:
            for number in range(1, size + 1):
                content, notes = corpus(number, size, source)
                anchor = identity(source, number, size)
                timestamp = EPOCH + number
                if source == 'notes':
                    LibraryService.put(records, 'library_item', {'id': anchor, 'account': ACCOUNT,
                        'kind': 'message', 'folder_id': 'folder', 'title': f'研究条目{number}',
                        'content': content, 'notes': notes, 'tags': ['合成记录'], 'verified': False,
                        'source': {'username': 'friend', 'anchor': identity('chat', number, size)},
                        'report': None, 'created_at': timestamp, 'updated_at': timestamp})
                    continue
                rowid = SOURCES.index(source) * size + number
                username = f'conversation{number % 20}'
                kind = {'chat': 'text', 'documents': 'file', 'ocr': 'image'}[source]
                payload = {'id': anchor, 'username': username,
                    'content': content if source == 'chat' else '附件' if source == 'documents' else '图片',
                    'title': '' if source == 'chat' else f'archive_{number}.pdf' if source == 'documents' else '',
                    'createTime': timestamp, 'renderType': kind, 'type': {'chat': 1, 'documents': 49, 'ocr': 3}[source],
                    'serverId': rowid, 'senderUsername': f'sender{number % 5}'}
                token_text = _to_char_token_text(payload['content'] + '\n' + payload['title'])
                values = (rowid, token_text, username, kind, timestamp, rowid, rowid, rowid,
                          payload['type'], 'message_0', 'Msg_bench', payload['senderUsername'], 0, 0)
                db.execute('INSERT INTO message_meta VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', values)
                db.execute('INSERT INTO message_fts(rowid,text,username,render_type,create_time,sort_seq,local_id,server_id,local_type,db_stem,table_name,sender_username,is_hidden,is_official,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (*values, json.dumps(payload, ensure_ascii=False)))
                if source != 'chat':
                    cache_id = attachment_key(ACCOUNT, username, anchor)
                    cache = {'id': cache_id, 'text': content, 'complete': number % 23 != 0,
                        'status': 'complete' if number % 23 else 'partial',
                        'extraction_method': 'document_text' if source == 'documents' else 'local_ocr',
                        'file_hash': hashlib.sha256(f'{source}:{number}'.encode()).hexdigest(), 'version': 2,
                        'message_signature': attachment_message_signature(normalize(ACCOUNT, username, payload)),
                        'ocr_units': 1 if source == 'ocr' else 0,
                        'ocr_engine': 'synthetic-cached-no-inference' if source == 'ocr' else None}
                    records.execute('INSERT INTO records VALUES(?,?,?,?,?)',
                        ('local_media_text', cache_id, ACCOUNT, json.dumps(cache, ensure_ascii=False), timestamp))
        _create_message_meta_indexes(db)
    return store, path


def scenarios(size):
    return [
        ('common', '项目进度', lambda n: True, {}),
        ('chinese', '电池回收', lambda n: n % 5 == 0, {}),
        ('case_insensitive', 'milestoneready', lambda n: n % 7 == 0, {}),
        ('exact_identifier', 'zx-42', lambda n: n % 11 == 0, {}),
        ('exact_amount', '10.50', lambda n: n % 11 == 0, {}),
        ('terms_across_text', '预算审批 周五提交', lambda n: n % 13 == 0, {}),
        ('rare', '稀有凭据R-ONLY', lambda n: n == size // 2, {}),
        ('missing', '不存在的SyntheticToken', lambda n: False, {}),
        ('symbol_literal', '👍', lambda n: n % 17 == 0, {}),
        ('next_page', '项目进度', lambda n: True, {'offset': 20}),
        ('deep_page', '项目进度', lambda n: True, {'offset': size - 25}),
        ('last_page', '项目进度', lambda n: True, {'offset': size - 5}),
        ('time_range', '项目进度', lambda n: size // 3 <= n <= size // 2,
            {'start_time': EPOCH + size // 3, 'end_time': EPOCH + size // 2}),
    ]


def result_ids(response, source):
    field = 'library_item_id' if source == 'notes' else 'anchor'
    return [item[field] for item in response['items']]


def result_checksum(response, *, snippets=True):
    items = response['items'] if snippets else [
        {key: value for key, value in item.items() if key != 'snippet'} for item in response['items']]
    return digest({'items': items, 'has_more': response['has_more'], 'next_offset': response['next_offset'],
                   'coverage': response['coverage']})


def check(response, source, expected, offset, limit, scenario, scoped_numbers):
    page = expected[offset:offset + limit]
    assert response['status'] == 'ready', (source, response['status'])
    assert result_ids(response, source) == page, (source, 'result identity differs from fixed corpus oracle')
    assert response['has_more'] == (offset + len(page) < len(expected)), (source, 'has_more')
    assert response['next_offset'] == offset + len(page), (source, 'next_offset')
    fields = ['extracted_text'] if source in ('documents', 'ocr') else ['content']
    if source == 'notes' and scenario == 'terms_across_text':
        fields.append('notes')
    assert all(item['source'] == source and item['matched_fields'] == fields for item in response['items'])
    scoped = len(scoped_numbers)
    coverage = response['coverage']
    assert coverage['available'] == scoped * (3 if source == 'chat' else 2 if source in ('documents', 'ocr') else 1)
    assert coverage['searched'] == scoped * (3 if source == 'chat' else 1)
    if source in ('documents', 'ocr'):
        partial = sum(number % 23 == 0 for number in scoped_numbers) * 2
        assert coverage['counts'] == {'complete': scoped * 2 - partial, 'partial': partial, 'no_text': 0,
            'not_extracted': 0, 'stale': 0, 'document_text': scoped, 'local_ocr': scoped}
    return result_checksum(response)


def profile_query(search, store, source, query='不存在的SyntheticToken'):
    profiler = cProfile.Profile()
    profiler.runcall(search.search_source, store, ACCOUNT, source=source, q=query)
    statistics = pstats.Stats(profiler)
    rows = sorted(statistics.stats.items(), key=lambda row: row[1][3], reverse=True)[:12]
    return [{'function': f'{Path(filename).name}:{line}:{name}', 'calls': values[1],
             'self_ms': round(values[2] * 1000, 3), 'cumulative_ms': round(values[3] * 1000, 3)}
            for (filename, line, name), values in rows]


def benchmark(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.environ['WECHAT_TOOL_DATA_DIR'] = str(output / 'runtime')
    os.environ['WECHAT_TOOL_OUTPUT_DIR'] = str(output / 'runtime' / 'output')
    from wechat_decrypt_tool import unified_search as search

    source_files = ('unified_search.py', 'attachment_service.py', 'chat_helpers.py', 'chat_search_index.py', 'ai/storage.py', 'library_service.py')
    source_hashes = {name: hashlib.sha256((ROOT / 'src' / 'wechat_decrypt_tool' / name).read_bytes()).hexdigest()
                     for name in source_files}
    for name in ('unified_search.py', 'attachment_service.py', 'chat_helpers.py'):
        (output / name.replace('.py', '_snapshot.py')).write_bytes((ROOT / 'src' / 'wechat_decrypt_tool' / name).read_bytes())
    report = {'fixture_version': FIXTURE_VERSION, 'dataset': 'synthetic_only', 'sizes_per_source': args.sizes,
        'iterations': args.iterations, 'entrypoint': 'unified_search.search_source',
        'benchmark_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'source_sha256': source_hashes,
        'limits': ['service only; excludes HTTP, UI, readiness, source freshness IO and index builds',
                   'cached synthetic extraction text; no OCR or model inference',
                   'one warm-up per case; OS file cache is not cleared',
                   'sequential source requests; does not measure concurrent tabs or users'],
        'cases': [], 'datasets': [], 'profiles': {}, 'profiles_common': {}}
    for size in args.sizes:
        began = time.perf_counter()
        store, path = seed(output / f'corpus-{size}', size)
        report['datasets'].append({'size_per_source': size, 'messages': size * 3, 'library_items': size,
            'cached_extractions': size * 2, 'seed_seconds': round(time.perf_counter() - began, 3),
            'search_db_bytes': path.stat().st_size, 'records_db_bytes': store.path.stat().st_size})
        with ExitStack() as patches:
            patches.enter_context(patch.object(search, '_resolve_account_dir', lambda account: path.parent))
            patches.enter_context(patch.object(search, 'get_chat_search_index_db_path', lambda account_dir: path))
            patches.enter_context(patch.object(search, 'ready_index', lambda account_dir:
                ({'ready': True, 'upToDate': True, 'source': 'synthetic_fixture'}, 'success', '')))
            for source in SOURCES:
                for label, query, matches, options in scenarios(size):
                    expected = [identity(source, number, size) for number in range(size, 0, -1) if matches(number)]
                    scoped_numbers = [number for number in range(1, size + 1)
                        if options.get('start_time', EPOCH) <= EPOCH + number <= options.get('end_time', EPOCH + size)]
                    offset, limit = options.get('offset', 0), 20
                    call = dict(source=source, q=query, limit=limit, **options)
                    warm = search.search_source(store, ACCOUNT, **call)
                    checksum = check(warm, source, expected, offset, limit, label, scoped_numbers)
                    times, service_times, response_bytes = [], [], []
                    for _ in range(args.iterations):
                        began = time.perf_counter()
                        result = search.search_source(store, ACCOUNT, **call)
                        times.append((time.perf_counter() - began) * 1000)
                        assert check(result, source, expected, offset, limit, label, scoped_numbers) == checksum, 'non-deterministic results'
                        service_times.append(result['elapsed_ms'])
                        response_bytes.append(len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()))
                    report['cases'].append({'size': size, 'source': source, 'scenario': label,
                        'expected_total': len(expected), 'returned': len(warm['items']), 'offset': offset,
                        'identity_checksum': digest(expected[offset:offset + limit]), 'result_checksum': checksum,
                        'non_snippet_checksum': result_checksum(warm, snippets=False),
                        'snippet_checksums': [digest(item['snippet']) for item in warm['items']],
                        'latency': distribution(times), 'service_elapsed': distribution(service_times),
                        'response_bytes': {'min': min(response_bytes), 'max': max(response_bytes)},
                        'coverage': warm['coverage']})
                print(json.dumps({'size': size, 'source': source, 'checked_cases': len(scenarios(size))}), flush=True)
                if size == max(args.sizes):
                    report['profiles'][source] = profile_query(search, store, source)
                    report['profiles_common'][source] = profile_query(search, store, source, '项目进度')
        (output / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding='utf-8'))
        assert baseline['fixture_version'] == FIXTURE_VERSION and baseline['sizes_per_source'] == args.sizes
        assert len(baseline['cases']) == len(report['cases'])
        differences, snippet_changes = [], []
        for before, after in zip(baseline['cases'], report['cases'], strict=True):
            for key in ('size', 'source', 'scenario', 'expected_total', 'returned', 'offset', 'identity_checksum'):
                assert before[key] == after[key], f'baseline mismatch: {after["source"]}/{after["scenario"]}/{key}'
            if before['result_checksum'] != after['result_checksum']:
                assert args.allow_snippet_changes, f'baseline result mismatch: {after["source"]}/{after["scenario"]}'
                assert before['non_snippet_checksum'] == after['non_snippet_checksum'], 'non-snippet result changed'
                snippet_changes.append({**{key: after[key] for key in ('size', 'source', 'scenario')},
                    'changed_items': sum(first != second for first, second in
                                         zip(before['snippet_checksums'], after['snippet_checksums'], strict=True))})
            differences.append({**{key: after[key] for key in ('size', 'source', 'scenario')},
                'before_p50_ms': before['latency']['p50_ms'], 'after_p50_ms': after['latency']['p50_ms'],
                'before_p95_ms': before['latency']['p95_ms'], 'after_p95_ms': after['latency']['p95_ms']})
        report['comparison'] = {'identical_results': not snippet_changes, 'identical_non_snippet_results': True,
                                'snippet_changes': snippet_changes, 'cases': differences}
    report['assertions'] = {'fixed_corpus_ids': True, 'paging': True, 'matched_fields': True,
                          'coverage_counts': True, 'deterministic_results_and_coverage': True}
    (output / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'summary': str(output / 'summary.json'), 'checked_cases': len(report['cases'])}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path, help='New isolated output directory; existing paths are rejected')
    parser.add_argument('--sizes', nargs='+', type=int, default=[1000, 10000])
    parser.add_argument('--iterations', type=int, default=5)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--allow-snippet-changes', action='store_true',
                        help='Explicitly permit and report snippet-only changes; all other result fields must match')
    arguments = parser.parse_args()
    if arguments.iterations < 1 or any(size < 100 for size in arguments.sizes):
        parser.error('iterations must be positive and every corpus size must be at least 100')
    benchmark(arguments)
