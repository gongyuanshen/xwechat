"""OCR process isolation. Workers never read account data or publish extraction caches."""
import logging
import multiprocessing as mp
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

from .local_acceleration_runtime import AccelerationError, PROVIDERS, failure_category, load_runtime, profile_evidence


def _warmup(engine, backend):
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new('RGB', (1000, 180), 'white')
    ImageDraw.Draw(image).text((30, 50), 'Receipt ZX739201', font=ImageFont.load_default(size=48), fill='black')
    for sample in (image, image.rotate(180)):
        result = engine(sample)
        if result.txts is None or 'ZX739201' not in '\n'.join(result.txts):
            raise AccelerationError('OCR 固定验证样例识别结果不匹配。', 'model')
    stages = {}
    for stage, attribute in [('Det', 'text_det'), ('Cls', 'text_cls'), ('Rec', 'text_rec')]:
        session = getattr(engine, attribute).session.session
        stages[stage] = profile_evidence(session.end_profiling(), PROVIDERS[backend])
        if backend == 'cpu':
            stages[stage]['gpu_nodes'] = 0
    return {'stages': stages, 'gpu_nodes': sum(v['gpu_nodes'] for v in stages.values()) if backend != 'cpu' else 0,
            'cpu_nodes': sum(v['cpu_nodes'] for v in stages.values()), 'probe_version': 1}


def ocr_worker(pipe, choice):
    backend = choice['backend']
    try:
        def no_network(event, args):
            if event == 'socket.connect':
                raise AccelerationError('本地 OCR 禁止联网下载模型或字典。', 'config')
        sys.addaudithook(no_network)
        device = None
        if backend != 'cpu':
            from .gpu_devices import resolve_device
            device = resolve_device(choice['device_identity'], backend)
            if device['driver'] != choice['fingerprint']['driver']:
                raise AccelerationError('OCR 显卡驱动与验证记录不一致，请重新检测。', 'config')
        ort = load_runtime(backend, choice['runtime_root'])
        from .local_ocr import _load_engine
        with tempfile.TemporaryDirectory(prefix='wechat-ocr-validation-') as profile_root:
            engine = _load_engine(backend, device['dxgi_index'] if device is not None else 0, profile_root)
            evidence = _warmup(engine, backend)
        if device is not None:
            current = resolve_device(choice['device_identity'], backend)
            if any(current[key] != device[key] for key in ('identity', 'luid', 'dxgi_index', 'driver')):
                raise AccelerationError('OCR 初始化期间显卡身份或驱动发生变化。', 'config')
        # Profiling ended above, before any caller-provided image enters the pipe.
        pipe.send({'ready': True, **evidence, 'runtime': ort.__version__, 'runtime_file': str(Path(ort.__file__).resolve()),
                   'pid': os.getpid(), 'device_id': device['dxgi_index'] if device is not None else None})
        while True:
            request = pipe.recv()
            if request is None:
                break
            result = engine(request)
            pipe.send({'text': '' if result.txts is None else '\n'.join(result.txts)})
    except Exception as error:
        logging.getLogger(__name__).exception('OCR worker failed: backend=%s', backend)
        pipe.send({'error': failure_category(error), 'message': str(error), 'type': type(error).__name__})
    finally:
        pipe.close()


class OcrWorker:
    def __init__(self):
        self.process = self.pipe = self.key = self.ready = None
        self.lock = threading.RLock()

    def close(self):
        with self.lock:
            if self.process is not None:
                if self.process.is_alive():
                    self.process.terminate()
                if self.process.pid is not None:
                    self.process.join(timeout=3)
                if self.process.is_alive():
                    raise AccelerationError('OCR 进程未能退出。', 'process')
                self.process.close()
            if self.pipe is not None:
                self.pipe.close()
            self.process = self.pipe = self.key = self.ready = None

    def _receive(self, cancelled=None, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancelled and cancelled():
                raise AccelerationError('OCR 验证已取消。', 'cancelled')
            if self.pipe.poll(.1):
                try:
                    response = self.pipe.recv()
                except (EOFError, OSError) as error:
                    raise AccelerationError('OCR 进程在返回结果前退出。', 'process') from error
                if 'error' in response:
                    if response['type'] == 'OcrUnavailableError':
                        from .local_ocr import OcrUnavailableError
                        raise OcrUnavailableError(response['message'])
                    raise AccelerationError(response['message'], response['error'])
                return response
            if not self.process.is_alive():
                raise AccelerationError(f'OCR 进程异常退出，exit={self.process.exitcode}。', 'process')
        raise AccelerationError('OCR 进程响应超时。', 'timeout')

    def start(self, choice, cancelled=None):
        from .local_ocr import ocr_model_spec
        from .local_acceleration import model_key
        key = (choice['backend'], choice['runtime_root'], choice['device_identity'],
               choice['generation'], model_key(ocr_model_spec()))
        with self.lock:
            if cancelled and cancelled():
                raise AccelerationError('OCR 验证已取消。', 'cancelled')
            if self.key == key:
                return self.ready
            self.close()
            context = mp.get_context('spawn')
            self.pipe, child = context.Pipe()
            self.process = context.Process(target=ocr_worker, args=(child, choice), daemon=True)
            try:
                self.process.start()
                child.close()
                self.ready = self._receive(cancelled)
            except Exception:
                child.close()
                self.close()
                raise
            self.key = key
            return self.ready

    def recognize(self, image, cancelled=None):
        with self.lock:
            try:
                self.pipe.send(image)
                return self._receive(cancelled)['text']
            except (OSError, EOFError) as error:
                self.close()
                raise AccelerationError('OCR 进程通信失败。', 'process') from error
            except Exception:
                self.close()
                raise
