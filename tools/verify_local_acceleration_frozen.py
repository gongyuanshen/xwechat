"""Exercise real production search/OCR workers from a frozen Windows executable."""
import argparse
import json
import multiprocessing
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', required=True)
    parser.add_argument('--models-root', required=True)
    parser.add_argument('--device-identity', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    from wechat_decrypt_tool.local_search.catalog import model_spec, model_dir
    from wechat_decrypt_tool.local_search.inference import LocalInference
    from wechat_decrypt_tool.local_ocr_inference import OcrWorker
    from wechat_decrypt_tool.gpu_devices import resolve_device
    from PIL import Image, ImageDraw, ImageFont

    report = {'frozen': bool(getattr(sys, 'frozen', False)), 'search': [], 'ocr': []}
    search = LocalInference(dml_root=args.runtime_root)
    ocr = OcrWorker()
    try:
        spec = model_spec('bge-small-zh')
        for backend in ('cpu', 'dml'):
            vectors = search.encode(model_dir(args.models_root, spec['id']), spec, ['离线桌面模型验证'], backend,
                                    device_identity=args.device_identity)
            assert len(vectors[0]) == spec['dimension'] and search.status['actual_device'] == backend
            report['search'].append(dict(search.status))
            choice = {'backend': backend, 'runtime_root': args.runtime_root if backend == 'dml' else None,
                      'device_identity': args.device_identity if backend == 'dml' else None, 'generation': 0}
            if backend != 'cpu':
                choice['fingerprint'] = {'driver': resolve_device(args.device_identity, backend)['driver']}
            ready = ocr.start(choice)
            image = Image.new('RGB', (1000, 180), 'white')
            ImageDraw.Draw(image).text((30, 50), 'Receipt ZX739201', font=ImageFont.load_default(size=48), fill='black')
            result = ocr.recognize(image)
            assert 'ZX739201' in result
            report['ocr'].append({'backend': backend, **ready, 'text': result})
        report['status'] = 'passed'
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'frozen': report['frozen'], 'status': report['status']}, ensure_ascii=False))
    finally:
        search.close()
        ocr.close()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
