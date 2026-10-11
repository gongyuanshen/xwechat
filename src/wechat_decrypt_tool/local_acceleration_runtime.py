"""Worker-only ORT loading and explicit execution evidence; never selects a device."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys
from collections import Counter


PROVIDERS = {'cpu': 'CPUExecutionProvider', 'cuda': 'CUDAExecutionProvider', 'dml': 'DmlExecutionProvider'}
GPU_FAILURES = frozenset({'gpu_init', 'gpu_execution', 'gpu_memory'})
_dll_handles = []


class AccelerationError(RuntimeError):
    def __init__(self, message, category='runtime'):
        super().__init__(message)
        self.category = category


def failure_category(error):
    """Only documented device failures permit the parent's one CPU retry."""
    current = error
    while current is not None:
        if hasattr(current, 'category'):
            return current.category
        if isinstance(current, ValueError):
            return 'input'
        message = str(current).lower()
        if any(code in message for code in ('dxgi_error_device_removed', 'dxgi_error_device_hung',
                                            'dxgi_error_device_reset', '0x887a0005', '0x887a0006', '0x887a0007')):
            return 'gpu_execution'
        if ('cuda failure' in message or 'cudaerror' in message or 'dml' in message or 'directml' in message):
            if 'out of memory' in message or 'e_outofmemory' in message or '0x8007000e' in message:
                return 'gpu_memory'
            if 'cuda failure' in message or 'cudaerror' in message:
                return 'gpu_execution'
        if type(current).__name__ in {'InvalidGraph', 'NoSuchFile', 'InvalidProtobuf'}:
            return 'model'
        current = current.__cause__
    return 'runtime'


def load_runtime(backend, runtime_root=None):
    if backend not in PROVIDERS:
        raise AccelerationError('不支持的推理后端：' + str(backend), 'config')
    if backend != 'cpu':
        if not runtime_root:
            raise AccelerationError('GPU 运行组件尚未安装', 'gpu_init')
        root = Path(runtime_root).resolve()
        package = root / 'onnxruntime'
        if not (package / '__init__.py').is_file():
            raise AccelerationError('GPU 运行组件目录不完整：' + str(root), 'config')
        if 'onnxruntime' in sys.modules:
            if not Path(sys.modules['onnxruntime'].__file__).resolve().is_relative_to(root):
                raise AccelerationError('推理进程已加载其他 ONNX Runtime，必须重启进程', 'config')
        else:
            # The closed component contains its own Python dependencies as well as ORT.
            # Workers are separate processes; this never changes the application's CPU runtime.
            sys.path.insert(0, str(root))
            installed = root / 'installed.json'
            if installed.is_file():
                from .local_search.gpu import validate_application_dependencies
                validate_application_dependencies(json.loads(installed.read_text(encoding='utf-8')))
            if os.name == 'nt':
                for folder in [package / 'capi', *root.rglob('bin')]:
                    if folder.is_dir():
                        _dll_handles.append(os.add_dll_directory(str(folder)))
            # Resolve the package explicitly, including when a frozen importer precedes PathFinder.
            spec = importlib.machinery.PathFinder.find_spec('onnxruntime', [str(root)])
            if spec is None:
                raise AccelerationError('找不到指定 ONNX Runtime 包', 'config')
            module = importlib.util.module_from_spec(spec)
            sys.modules['onnxruntime'] = module
            spec.loader.exec_module(module)
        import onnxruntime as ort
        import onnxruntime.capi.onnxruntime_pybind11_state as native
        for path in (ort.__file__, native.__file__):
            if not Path(path).resolve().is_relative_to(root):
                raise AccelerationError('ONNX Runtime 模块来源不匹配：' + str(path), 'config')
    else:
        import onnxruntime as ort
    if PROVIDERS[backend] not in ort.get_available_providers():
        raise AccelerationError('运行组件缺少 ' + PROVIDERS[backend], 'gpu_init' if backend != 'cpu' else 'config')
    return ort


def create_session(model_path, backend, device_id=0, profile_prefix=None):
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    if backend == 'dml':
        options.enable_mem_pattern = False
    if profile_prefix is not None:
        options.enable_profiling = True
        options.profile_file_prefix = str(profile_prefix)
    provider = PROVIDERS[backend]
    if backend == 'cpu':
        providers = [provider]
    else:
        provider_options = {'device_id': device_id}
        if backend == 'cuda':
            provider_options.update(use_tf32=0, arena_extend_strategy='kSameAsRequested', gpu_mem_limit=2 * 1024**3)
            ort.preload_dlls(directory='')
        providers = [(provider, provider_options), 'CPUExecutionProvider']
    session = ort.InferenceSession(str(model_path), sess_options=options, providers=providers, enable_fallback=False)
    session.disable_fallback()
    if provider not in session.get_providers():
        raise AccelerationError('指定后端未初始化：' + provider, 'gpu_init')
    return session


def profile_evidence(profile_path, provider):
    events = json.loads(Path(profile_path).read_text(encoding='utf-8'))
    counts = Counter(event['args']['provider'] for event in events
                     if event.get('cat') == 'Node' and event.get('args', {}).get('provider'))
    if counts[provider] == 0:
        raise AccelerationError('没有发现指定 GPU 的实际执行记录：' + provider, 'gpu_init')
    return {'providers': dict(counts), 'gpu_nodes': counts[provider], 'cpu_nodes': counts['CPUExecutionProvider']}
