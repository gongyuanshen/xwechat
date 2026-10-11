"""Explicit, offline OCR using the models shipped in the pinned RapidOCR wheel."""
import hashlib
import io
from pathlib import Path
import threading
import warnings
from contextlib import contextmanager

from PIL import Image, ImageOps

from .local_acceleration import get_local_acceleration
from .local_acceleration_runtime import AccelerationError, GPU_FAILURES, failure_category, create_session


IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}
ENGINE_NAME = 'RapidOCR 3.10.0 / PP-OCRv6 Det+Rec / PP-OCRv2 Cls'
# RapidOCR 3.10.0 MODEL_LICENSES.md: these recognition weights include the
# character dictionary, so neither models nor dictionaries need downloading.
MODELS = {
    'Det': ('PP-OCRv6_det_small.onnx', '090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f'),
    'Cls': ('ch_ppocr_mobile_v2.0_cls_mobile.onnx', 'e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c'),
    'Rec': ('PP-OCRv6_rec_small.onnx', '6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884'),
}
_runner = None
_last_runtime = None
_lock = threading.RLock()
_scope = threading.local()


class OcrUnavailableError(AccelerationError):
    def __init__(self, message):
        super().__init__(message, 'model')


def ocr_model_spec():
    return {'id': 'rapidocr-ppocrv6', 'revision': 'rapidocr-3.10.0-probe-1',
            'files': {stage: {'name': name, 'sha256': value} for stage, (name, value) in MODELS.items()},
            'config': {'threads': 2, 'execution': 'sequential', 'dml_mem_pattern': False}}


class _StageConfig(dict):
    __getattr__ = dict.__getitem__


def _load_engine(backend='cpu', device_id=0, profile_root=None):
    try:
        import rapidocr
    except ImportError as error:
        raise OcrUnavailableError('本地 OCR 组件缺失，请重新安装包含 OCR 的应用或同步项目依赖。') from error
    root = Path(rapidocr.__file__).parent / 'models'
    paths = {}
    for stage, (filename, digest) in MODELS.items():
        path = root / filename
        if not path.is_file():
            raise OcrUnavailableError(f'本地 OCR 模型缺失：{filename}。请重新安装或同步项目依赖。')
        with path.open('rb') as source:
            if hashlib.file_digest(source, 'sha256').hexdigest() != digest:
                raise OcrUnavailableError(f'本地 OCR 模型校验失败：{filename}。请重新安装或同步项目依赖。')
        paths[stage] = path
    from omegaconf import OmegaConf
    from rapidocr.ch_ppocr_det import TextDetector
    from rapidocr.ch_ppocr_cls import TextClassifier
    from rapidocr.ch_ppocr_rec import TextRecognizer
    engine = rapidocr.RapidOCR(params={'Global.log_level': 'warning'})
    for stage, attribute, constructor in [('Det', 'text_det', TextDetector), ('Cls', 'text_cls', TextClassifier), ('Rec', 'text_rec', TextRecognizer)]:
        if getattr(engine, attribute) is not None:
            raise AccelerationError('RapidOCR 初始化协议变化，请重新验证 Session 注入。', 'config')
        session = create_session(paths[stage], backend, device_id,
                                 Path(profile_root) / stage if profile_root is not None else None)
        if stage == 'Rec' and not session.get_modelmeta().custom_metadata_map.get('character'):
            raise OcrUnavailableError('本地 OCR 模型缺少内嵌识别字典。')
        cfg = _StageConfig(OmegaConf.to_container(engine.cfg[stage], resolve=True))
        cfg.update(session=session, model_path=str(paths[stage]), model_root_dir=root,
                   engine_cfg=engine.cfg.EngineConfig.onnxruntime, font_path=engine.cfg.Global.font_path)
        component = constructor(cfg)
        if component.session.session is not session:
            raise AccelerationError(f'OCR {stage} 未使用指定 Session。', 'config')
        setattr(engine, attribute, component)
    return engine


def _prepare_image(data):
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as original:
            if getattr(original, 'n_frames', 1) != 1:
                raise ValueError('本地 OCR 暂不支持动态图或多帧图片，请先保存为单张静态图片。')
            image = ImageOps.exif_transpose(original).convert('RGBA')
            background = Image.new('RGBA', image.size, 'white')
            image = Image.alpha_composite(background, image).convert('RGB')
    return image


class OcrJob:
    def __init__(self, manager, runner):
        self.manager, self.runner = manager, runner
        self.model = ocr_model_spec()
        self.settings = dict(manager.ocr_settings())
        self.choice = manager.select('ocr', self.model, self.settings['device'], self.settings['device_identity'])
        self.ready = None
        self.device_history = []
        self.using_fallback, self.reason = self.choice.get('using_fallback', False), self.choice['reason']

    def _run(self, image):
        if self.ready is None:
            self.ready = self.runner.start(self.choice)
            if _last_runtime is not None and _last_runtime[2].get('pid') != self.ready['pid']:
                _record_inactive()
            if self.choice['backend'] != 'cpu':
                self.manager.validated(self.choice, self.ready)
        return self.runner.recognize(image)

    def recognize(self, image):
        global _last_runtime
        try:
            result = self._run(image)
        except Exception as error:
            self.runner.close()
            _record_inactive()
            self.ready = None
            self.manager.failed(self.choice, error)
            if self.settings['device'] != 'auto' or self.choice['backend'] == 'cpu' or failure_category(error) not in GPU_FAILURES:
                raise
            self.using_fallback, self.reason = True, str(error)
            self.choice = self.manager.select('ocr', self.model, 'cpu', None)
            # Only this failed, unreturned image is replayed; the scope then stays CPU.
            result = self._run(image)
        if not self.device_history or self.device_history[-1] != self.choice['backend']:
            self.device_history.append(self.choice['backend'])
        status = self.metadata()
        self.manager.record_runtime('ocr', '', self.model, status)
        _last_runtime = (self.manager, self.model, status)
        return result

    @property
    def engine_name(self):
        devices = ' → '.join('DirectML' if value == 'dml' else 'CPU' for value in self.device_history)
        if self.choice['backend'] == 'dml' and self.ready and self.ready['cpu_nodes']:
            devices += ' + 部分 CPU'
        if not devices:
            devices = '未运行'
        return ENGINE_NAME + ' / ' + devices

    def metadata(self):
        return {**(self.ready or {}), 'actual_device': self.choice['backend'] if self.device_history else None,
                'device_identity': self.choice['device_identity'], 'device_name': self.choice['device_name'],
                'using_fallback': self.using_fallback, 'reason': self.reason,
                'device_history': list(self.device_history), 'settings_revision': self.settings['revision'],
                'requested_device': self.settings['device'], 'model': self.model,
                'active': bool(self.ready and self.device_history),
                'evidence': {key: self.ready[key] for key in ('gpu_nodes', 'cpu_nodes', 'stages')} if self.ready else {}}


def _record_inactive():
    global _last_runtime
    if _last_runtime is not None:
        manager, model, status = _last_runtime
        manager.record_runtime('ocr', '', model, {**status, 'active': False})
        _last_runtime = None


@contextmanager
def ocr_job():
    """Fix configuration and serialize the full attachment, including all PDF pages."""
    global _runner
    with _lock:
        if getattr(_scope, 'job', None) is not None:
            yield _scope.job
            return
        if _runner is None:
            from .local_ocr_inference import OcrWorker
            _runner = OcrWorker()
        job = OcrJob(get_local_acceleration(), _runner)
        _scope.job = job
        try:
            yield job
        finally:
            del _scope.job


def recognize_image(data: bytes) -> str:
    """A valid image without text returns ''; failures are never empty successes."""
    image = _prepare_image(data)
    job = getattr(_scope, 'job', None)
    if job is not None:
        return job.recognize(image)
    with ocr_job() as job:
        return job.recognize(image)


def validate_ocr(choice, cancelled=None):
    """Validate generated non-private input without recording a business run."""
    from .local_ocr_inference import OcrWorker
    worker = OcrWorker()
    try:
        return worker.start(choice, cancelled)
    finally:
        worker.close()


def close_ocr():
    global _runner
    with _lock:
        if _runner is not None:
            _runner.close()
            _runner = None
        _record_inactive()
