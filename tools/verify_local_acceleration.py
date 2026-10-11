"""Non-private embedding acceptance. Components/models must be prepared explicitly."""
import argparse
import json
from pathlib import Path
import time


TEXTS = ['项目进度延期，改为下周二交付。', '周末一起吃饭，周六下午六点见。', '发票编号 20261011，金额 128.50 元。']
QUERIES = ['项目什么时候交付？', '周末聚餐的时间？', '发票金额是多少？']


def run(args):
    from wechat_decrypt_tool.local_acceleration_runtime import load_runtime, create_session, profile_evidence, PROVIDERS
    from wechat_decrypt_tool.local_search.catalog import CATALOG, model_dir, verify_model
    device_id = 0
    device = {'name': 'CPU', 'identity': None}
    if args.backend != 'cpu':
        from wechat_decrypt_tool.gpu_devices import resolve_device
        device = resolve_device(args.device_identity, args.backend)
        device_id = device['dxgi_index'] if args.backend == 'dml' else device['cuda_index']
    ort = load_runtime(args.backend, args.runtime_root)
    import numpy as np
    from tokenizers import Tokenizer
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {'backend': args.backend, 'runtime': ort.__version__, 'runtime_file': ort.__file__,
              'device': device, 'models': [], 'private_input': False}
    for spec in CATALOG:
        root = model_dir(args.models_root, spec['id'])
        verify_model(root, spec)
        tokenizer = Tokenizer.from_file(str(root / 'tokenizer.json'))
        tokenizer.enable_padding(pad_id=tokenizer.token_to_id('[PAD]') or tokenizer.token_to_id('<pad>') or 0)
        start = time.perf_counter()
        session = create_session(root / 'onnx/model.onnx', args.backend, device_id,
                                 output.parent / (args.backend + '-' + spec['id']))
        load_seconds = time.perf_counter() - start

        def encode(texts, query=False):
            prefix = spec['query_prefix'] if query else spec['passage_prefix']
            tokens = tokenizer.encode_batch([prefix + text for text in texts])
            arrays = {'input_ids': np.array([t.ids for t in tokens], dtype=np.int64),
                      'attention_mask': np.array([t.attention_mask for t in tokens], dtype=np.int64),
                      'token_type_ids': np.array([t.type_ids for t in tokens], dtype=np.int64)}
            vectors = session.run(None, {i.name: arrays[i.name] for i in session.get_inputs()})[0]
            if vectors.ndim == 3:
                if spec['pooling'] == 'cls':
                    vectors = vectors[:, 0]
                else:
                    mask = arrays['attention_mask'][..., None]
                    vectors = (vectors * mask).sum(axis=1) / mask.sum(axis=1)
            vectors = vectors.astype(np.float32)
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            assert vectors.shape == (len(texts), spec['dimension']) and np.isfinite(vectors).all() and (norms > 0).all()
            return vectors / norms

        start = time.perf_counter()
        passages = encode(TEXTS)
        cold = time.perf_counter() - start
        start = time.perf_counter()
        queries = encode(QUERIES, True)
        query_seconds = time.perf_counter() - start
        hits = (queries @ passages.T).argmax(axis=1).tolist()
        assert hits == [0, 1, 2], (spec['id'], hits)
        # Match indexing batch size, then vary length and batch shape in this same session.
        start = time.perf_counter()
        for _ in range(3):
            encode((TEXTS * 3)[:8])
        batch_seconds = (time.perf_counter() - start) / 3
        encode(['测试。' * 150])
        start = time.perf_counter()
        for _ in range(3):
            encode([QUERIES[0]], True)
        single_query_seconds = (time.perf_counter() - start) / 3
        profile = session.end_profiling()
        evidence = profile_evidence(profile, PROVIDERS[args.backend]) if args.backend != 'cpu' else {'profile': profile}
        record = {'id': spec['id'], 'revision': spec['revision'], 'load_seconds': load_seconds,
                  'cold_encode_seconds': cold, 'three_queries_seconds': query_seconds,
                  'single_query_seconds': single_query_seconds, 'batch8_seconds': batch_seconds,
                  'repetitions': 3, 'evidence': evidence, 'passages': passages.tolist(),
                  'queries': queries.tolist(), 'hits': hits}
        report['models'].append(record)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({k: v for k, v in record.items() if k not in {'passages', 'queries'}}, ensure_ascii=False), flush=True)
        del session
    if args.compare_with:
        report['comparison'] = compare_reports(json.loads(Path(args.compare_with).read_text(encoding='utf-8')), report)
    report['status'] = 'passed'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


def compare_reports(cpu, gpu):
    import numpy as np
    if cpu['backend'] != 'cpu' or gpu['backend'] == 'cpu':
        raise ValueError('Comparison requires a CPU baseline and a GPU result')
    baseline = {item['id']: item for item in cpu['models']}
    if set(baseline) != {item['id'] for item in gpu['models']}:
        raise ValueError('CPU/GPU model sets differ')
    results = []
    for item in gpu['models']:
        reference = baseline[item['id']]
        if reference['revision'] != item['revision']:
            raise ValueError('CPU/GPU model revisions differ')
        a, b = [np.asarray(item[key]) for key in ('passages', 'queries')], [np.asarray(reference[key]) for key in ('passages', 'queries')]
        delta = max(float(np.abs(x - y).max()) for x, y in zip(a, b))
        cosine = min(float(np.sum(x * y, axis=1).min()) for x, y in zip(a, b))
        hits = [(q @ p.T).argmax(axis=1).tolist() for p, q in (a, b, (a[0], b[1]), (b[0], a[1]))]
        if not np.isfinite(delta) or not np.isfinite(cosine) or delta > .001 or cosine < .99995 or any(h != [0, 1, 2] for h in hits):
            raise ValueError(f'CPU/GPU validation failed: {item["id"]}, max_abs={delta}, min_cosine={cosine}, hits={hits}')
        results.append({'id': item['id'], 'max_abs_error': delta, 'min_cosine': cosine, 'hits': hits})
    return results


def main(default_backend=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['cpu', 'cuda', 'dml'], required=default_backend is None, default=default_backend)
    parser.add_argument('--runtime-root')
    parser.add_argument('--device-identity')
    parser.add_argument('--models-root', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--compare-with', help='Previously verified CPU report; enforces numerical and mixed-index retrieval thresholds')
    options = parser.parse_args()
    if options.backend != 'cpu' and (not options.runtime_root or not options.device_identity):
        parser.error('GPU requires --runtime-root and --device-identity')
    run(options)


if __name__ == '__main__':
    main()
