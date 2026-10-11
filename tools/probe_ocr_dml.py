"""Offline CPU/DirectML OCR probe. Does not change product settings or data.

Example:
  .venv/Scripts/python.exe tools/probe_ocr_dml.py --mode compare \
    --runtime-root tmp/local-acceleration-validation/components/dml-ort1.24.4/cp311 --device-id 0

Acceptance thresholds and fixture contents are fixed before inference. Every
backend runs in its own process; CPU always uses the application environment.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback


REPO = Path(__file__).resolve().parents[1]
FIXTURE_VERSION = 'ocr-generated-v1'
BOX_ATOL = 2.0
SCORE_ATOL = 0.02
LINES = ['本地文字识别测试', 'Receipt ZX739201', '1234567890']


class StageConfig(dict):
    """RapidOCR stage constructors use both mapping and attribute access."""
    __getattr__ = dict.__getitem__


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def save_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def profile_summary(path, provider):
    events = json.loads(Path(path).read_text(encoding='utf-8'))
    nodes = [event for event in events if event.get('cat') == 'Node' and event.get('args', {}).get('provider')]
    counts = Counter(event['args']['provider'] for event in nodes)
    return {'path': str(Path(path).resolve()), 'provider_events': dict(counts),
            'provider_duration_us': {name: sum(e.get('dur', 0) for e in nodes if e['args']['provider'] == name) for name in counts},
            'requested_provider_executed': counts[provider] > 0,
            'operators': dict(Counter(e['args'].get('op_name', e['name']) for e in nodes))}


def compare_reports(cpu, dml):
    import numpy as np
    differences = []
    if len(cpu['samples']) != len(dml['samples']):
        return {'passed': False, 'differences': [{'error': 'sample count differs'}]}
    for left, right in zip(cpu['samples'], dml['samples']):
        result = {'id': left['id'], 'passed': True}
        for key in ('id', 'label', 'txts'):
            if left[key] != right[key]:
                result.update(passed=False, **{key: {'cpu': left[key], 'dml': right[key]}})
        for key, tolerance in [('boxes', BOX_ATOL), ('scores', SCORE_ATOL)]:
            a, b = np.asarray(left[key]), np.asarray(right[key])
            if a.shape != b.shape:
                result.update(passed=False, **{key: {'cpu_shape': list(a.shape), 'dml_shape': list(b.shape)}})
            else:
                delta = float(np.max(np.abs(a - b))) if a.size else 0.0
                result[key + '_max_abs_difference'] = delta
                if not np.isfinite(a).all() or not np.isfinite(b).all() or delta > tolerance:
                    result['passed'] = False
        differences.append(result)
    return {'passed': all(item['passed'] for item in differences),
            'box_atol_pixels': BOX_ATOL, 'score_atol': SCORE_ATOL, 'text_comparison': 'exact ordered lines',
            'differences': differences}


def make_samples(root):
    from PIL import Image, ImageDraw, ImageFont
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, NumberObject, DecodedStreamObject
    root.mkdir(parents=True, exist_ok=True)
    font_path = Path('C:/Windows/Fonts/msyh.ttc')
    font = ImageFont.truetype(str(font_path), 60)
    image = Image.new('RGB', (1100, 550), 'white')
    draw = ImageDraw.Draw(image)
    for index, text in enumerate(LINES):
        draw.text((60, 65 + index * 145), text, font=font, fill='black')
    rotated = image.rotate(180)
    blank = Image.new('RGB', image.size, 'white')
    for name, pic in [('text.png', image), ('rotated.png', rotated), ('blank.png', blank)]:
        pic.save(root / name)
    image.save(root / 'scanned.pdf', 'PDF', save_all=True, append_images=[rotated],
               creationDate='D:20261011000000Z', modDate='D:20261011000000Z')
    writer = PdfWriter()
    page = writer.add_blank_page(width=1100, height=700)
    scan = DecodedStreamObject()
    scan.set_data(image.tobytes())
    scan.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Image'),
                 NameObject('/Width'): NumberObject(image.width), NameObject('/Height'): NumberObject(image.height),
                 NameObject('/ColorSpace'): NameObject('/DeviceRGB'), NameObject('/BitsPerComponent'): NumberObject(8)})
    font_pdf = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                                 NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font_pdf)}),
                                                     NameObject('/XObject'): DictionaryObject({NameObject('/Scan'): writer._add_object(scan)})})
    commands = DecodedStreamObject()
    commands.set_data(b'BT /F1 24 Tf 60 640 Td (Native heading 2468) Tj ET\nq 1100 0 0 550 0 0 cm /Scan Do Q')
    page[NameObject('/Contents')] = writer._add_object(commands)
    writer.write(root / 'mixed.pdf')
    manifest = {'version': FIXTURE_VERSION, 'font_path': str(font_path), 'font_sha256': digest(font_path),
                'box_atol_pixels': BOX_ATOL, 'score_atol': SCORE_ATOL,
                'files': [{'name': name, 'sha256': digest(root / name), 'expected': expected}
                          for name, expected in [('text.png', [LINES]), ('rotated.png', [list(reversed(LINES))]),
                                                 ('blank.png', [[]]), ('scanned.pdf', [LINES, list(reversed(LINES))]),
                                                 ('mixed.pdf', [LINES])]]}
    save_json(root / 'manifest.json', manifest)
    return manifest


def make_engine(ort, backend, device_id, output_root):
    import rapidocr
    from omegaconf import OmegaConf
    from rapidocr.ch_ppocr_det import TextDetector
    from rapidocr.ch_ppocr_cls import TextClassifier
    from rapidocr.ch_ppocr_rec import TextRecognizer
    from wechat_decrypt_tool.local_ocr import MODELS
    root = Path(rapidocr.__file__).parent / 'models'
    engine = rapidocr.RapidOCR(params={'Global.log_level': 'warning'})
    if any(getattr(engine, attr) is not None for attr in ('text_det', 'text_cls', 'text_rec')):
        raise RuntimeError('RapidOCR is no longer lazy: session injection must be revalidated')
    sessions, evidence = {}, {}
    expected_provider = 'DmlExecutionProvider' if backend == 'dml' else 'CPUExecutionProvider'
    for stage, attr, constructor in [('Det', 'text_det', TextDetector), ('Cls', 'text_cls', TextClassifier), ('Rec', 'text_rec', TextRecognizer)]:
        filename, expected_digest = MODELS[stage]
        path = root / filename
        actual_digest = digest(path)
        if actual_digest != expected_digest:
            raise ValueError(f'OCR model SHA-256 mismatch: {path}')
        options = ort.SessionOptions()
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.enable_mem_pattern = False
        options.enable_cpu_mem_arena = False
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.enable_profiling = True
        options.profile_file_prefix = str(output_root / f'{backend}-{stage}')
        options.log_severity_level = 3
        providers = [('DmlExecutionProvider', {'device_id': device_id}), 'CPUExecutionProvider'] if backend == 'dml' else ['CPUExecutionProvider']
        started = time.perf_counter()
        session = ort.InferenceSession(str(path), sess_options=options, providers=providers, enable_fallback=False)
        session.disable_fallback()
        if expected_provider not in session.get_providers():
            raise RuntimeError(f'{stage} requested provider missing after construction: {session.get_providers()}')
        sessions[stage] = session
        if stage == 'Rec' and not session.get_modelmeta().custom_metadata_map.get('character'):
            raise RuntimeError('OCR recognition dictionary is missing from original model metadata')
        cfg = StageConfig(OmegaConf.to_container(engine.cfg[stage], resolve=True))
        cfg.update(session=session, model_path=str(path), model_root_dir=root,
                   engine_cfg=engine.cfg.EngineConfig.onnxruntime, font_path=engine.cfg.Global.font_path)
        component = constructor(cfg)
        if component.session.session is not session:
            raise RuntimeError(f'{stage} did not retain the injected real session')
        setattr(engine, attr, component)
        metadata = session.get_modelmeta().custom_metadata_map
        characters = metadata.get('character', '')
        evidence[stage] = {'model': str(path), 'sha256': actual_digest, 'providers': session.get_providers(),
                           'provider_options': session.get_provider_options(), 'session_identity_verified': True,
                           'initialization_seconds': time.perf_counter() - started,
                           'inputs': [{'name': v.name, 'shape': v.shape, 'type': v.type} for v in session.get_inputs()],
                           'metadata_keys': sorted(metadata), 'character_count': len(characters.splitlines()),
                           'character_sha256': hashlib.sha256(characters.encode()).hexdigest() if characters else None,
                           'execution_mode': 'ORT_SEQUENTIAL', 'enable_mem_pattern': False, 'enable_fallback': False}
        print(f'{backend}: {stage} session injected', flush=True)
    return engine, sessions, evidence


def prepared_image(data):
    import warnings
    from PIL import Image, ImageOps
    # Same EXIF/alpha/multiframe handling as local_ocr.recognize_image.
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as original:
            if getattr(original, 'n_frames', 1) != 1:
                raise ValueError('Probe requires a single image frame')
            image = ImageOps.exif_transpose(original).convert('RGBA')
            return Image.alpha_composite(Image.new('RGBA', image.size, 'white'), image).convert('RGB')


def worker(args):
    started = time.perf_counter()
    output_root = args.output_root
    report = {'status': 'running', 'backend': args.mode, 'device_id': args.device_id, 'samples': [],
              'repetitions': args.repetitions, 'fixture_version': FIXTURE_VERSION}
    sessions = {}
    try:
        if 'onnxruntime' in sys.modules:
            raise RuntimeError('ORT was imported before runtime selection')
        if args.mode == 'dml':
            sys.path.insert(0, str(args.runtime_root))
        sys.path.insert(0, str(REPO / 'src'))
        # A probe must never fetch models or dictionaries implicitly.
        def audit(event, audit_args):
            if event == 'socket.connect':
                raise RuntimeError('OCR probe network access is prohibited')
        sys.addaudithook(audit)
        import onnxruntime as ort
        import psutil
        actual_root = Path(ort.__file__).resolve()
        expected_root = args.runtime_root if args.mode == 'dml' else REPO / '.venv' / 'Lib' / 'site-packages'
        if not actual_root.is_relative_to(expected_root.resolve()):
            raise RuntimeError(f'ORT runtime isolation failed: {actual_root}')
        report['runtime'] = {'file': str(actual_root), 'version': ort.__version__,
                             'python': sys.version, 'executable': sys.executable,
                             'available_providers': ort.get_available_providers()}
        engine, sessions, report['stages'] = make_engine(ort, args.mode, args.device_id, output_root)
        report['cold_initialization_seconds'] = time.perf_counter() - started
        from wechat_decrypt_tool.ai.media import iter_document
        samples_root = output_root / 'samples'
        manifest = json.loads((samples_root / 'manifest.json').read_text(encoding='utf-8'))
        report['fixture_manifest'] = manifest
        for fixture in manifest['files']:
            path = samples_root / fixture['name']
            if digest(path) != fixture['sha256']:
                raise ValueError(f'Fixture hash changed: {path}')
            data = path.read_bytes()
            input_started = time.perf_counter()
            parts = list(iter_document(data, '.pdf', raw_images=True)) if path.suffix == '.pdf' else [{'label': '单图', 'image': data}]
            expected_index = 0
            for index, part in enumerate(parts):
                record = {'id': f'{path.name}:{index}', 'label': part['label'], 'boxes': [], 'scores': [],
                          'txts': [], 'seconds': [], 'stage_seconds': []}
                if 'image' in part:
                    image = prepared_image(part['image'])
                    record['image_size'] = list(image.size)
                    expected = fixture['expected'][expected_index]
                    expected_index += 1
                    for repetition in range(args.repetitions):
                        run_started = time.perf_counter()
                        result = engine(image)
                        record['seconds'].append(time.perf_counter() - run_started)
                        record['stage_seconds'].append([float(v) if v is not None else None for v in result.elapse_list])
                        txts = [] if result.txts is None else list(result.txts)
                        boxes = [] if result.boxes is None else result.boxes.tolist()
                        scores = [] if result.scores is None else [float(v) for v in result.scores]
                        if repetition and (record['txts'] != txts or record['boxes'] != boxes or record['scores'] != scores):
                            raise RuntimeError(f'Non-deterministic OCR result: {record["id"]}')
                        record.update(txts=txts, boxes=boxes, scores=scores)
                    record['expected'] = expected
                    record['expected_matches'] = record['txts'] == expected
                else:
                    record['txts'] = [part['text']]
                report['samples'].append(record)
                save_json(output_root / f'{args.mode}.json', report)
                print(f'{args.mode}: {record["id"]} {record["txts"]}', flush=True)
            report.setdefault('document_seconds', {})[path.name] = time.perf_counter() - input_started
        report['runtime']['loaded_native_modules'] = sorted({m.path for m in psutil.Process().memory_maps()
                                                             if any(token in m.path.lower() for token in ('onnxruntime', 'directml'))})
        native_root = expected_root / 'onnxruntime'
        for path in report['runtime']['loaded_native_modules']:
            if not Path(path).resolve().is_relative_to(native_root.resolve()):
                raise RuntimeError(f'Unexpected ORT/DirectML native module source: {path}')
        report['status'] = 'passed' if all(s.get('expected_matches', True) for s in report['samples']) else 'failed'
    except Exception as error:
        report.update(status='failed', error=str(error), traceback=traceback.format_exc())
    finally:
        provider = 'DmlExecutionProvider' if args.mode == 'dml' else 'CPUExecutionProvider'
        report['profiles'] = {stage: profile_summary(session.end_profiling(), provider) for stage, session in sessions.items()}
        if set(report['profiles']) != {'Det', 'Cls', 'Rec'} or not all(p['requested_provider_executed'] for p in report['profiles'].values()):
            report['status'] = 'failed'
            report['provider_evidence_error'] = 'All three stages must execute on the requested provider'
        report['total_seconds'] = time.perf_counter() - started
        save_json(output_root / f'{args.mode}.json', report)
    return 0 if report['status'] == 'passed' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['cpu', 'dml', 'compare'], default='compare')
    parser.add_argument('--runtime-root', type=Path)
    parser.add_argument('--device-id', type=int, default=0)
    parser.add_argument('--output-root', type=Path, default=REPO / 'tmp/local-acceleration-validation/ocr-probe')
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.output_root = args.output_root.resolve()
    if args.device_id < 0 or args.repetitions < 1:
        parser.error('device-id must be nonnegative and repetitions must be positive')
    if args.mode != 'cpu':
        if args.runtime_root is None or not (args.runtime_root / 'onnxruntime/__init__.py').is_file():
            parser.error('dml/compare requires an existing isolated --runtime-root')
        args.runtime_root = args.runtime_root.resolve()
    args.output_root.mkdir(parents=True, exist_ok=True)
    if args.worker:
        return worker(args)
    make_samples(args.output_root / 'samples')
    modes = ['cpu', 'dml'] if args.mode == 'compare' else [args.mode]
    codes = {}
    for mode in modes:
        command = [sys.executable, str(Path(__file__).resolve()), '--mode', mode, '--worker',
                   '--device-id', str(args.device_id), '--output-root', str(args.output_root),
                   '--repetitions', str(args.repetitions)]
        if mode == 'dml':
            command.extend(['--runtime-root', str(args.runtime_root)])
        with (args.output_root / f'{mode}.log').open('w', encoding='utf-8') as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env={**__import__('os').environ, 'PYTHONIOENCODING': 'utf-8'})
        codes[mode] = result.returncode
        print(f'{mode}: exit={result.returncode}; {args.output_root / (mode + ".json")}', flush=True)
    if args.mode == 'compare':
        cpu = json.loads((args.output_root / 'cpu.json').read_text(encoding='utf-8'))
        dml = json.loads((args.output_root / 'dml.json').read_text(encoding='utf-8'))
        comparison = compare_reports(cpu, dml)
        comparison['worker_returncodes'] = codes
        comparison['passed'] = comparison['passed'] and all(code == 0 for code in codes.values())
        save_json(args.output_root / 'comparison.json', comparison)
        print(json.dumps(comparison, ensure_ascii=False), flush=True)
        return 0 if comparison['passed'] else 1
    return codes[args.mode]


if __name__ == '__main__':
    raise SystemExit(main())
