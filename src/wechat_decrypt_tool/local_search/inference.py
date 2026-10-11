"""隔离推理进程；仅自动模式的明确 GPU 故障可重放当前未提交批次。"""
from ..ai.diagnostics import observed, event as diagnostic_event
import logging
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from ..ai.diagnostics import exception_fields
from ..local_acceleration_runtime import AccelerationError, GPU_FAILURES, failure_category

VALIDATION_TEXTS = ['项目进度延期，改为下周二交付。', '周末一起吃饭，周六下午六点见。', '发票编号 20261011，金额 128.50 元。']
VALIDATION_QUERIES = ['项目什么时候交付？', '周末聚餐的时间？', '发票金额是多少？']


class InferenceFailure(RuntimeError):
    def __init__(self, message, category='runtime'):
        super().__init__(message)
        self.category = category


def inference_worker(pipe, root, spec, device, device_id, gpu_root, device_identity=None, expected_driver=None):
    # 子进程只经 Pipe 回传元数据，由父进程统一落盘。
    logging.disable(logging.CRITICAL)
    try:
        os.environ['TOKENIZERS_PARALLELISM']='false'
        from .catalog import verify_model
        try: verify_model(root,spec,report=lambda name, fields:pipe.send({'diagnostic_event':name, **fields}))
        except (ValueError,OSError): raise InferenceFailure('模型文件缺失或损坏，请重新下载或导入','model') from None
        from ..local_acceleration_runtime import load_runtime, create_session, profile_evidence, PROVIDERS
        from ..gpu_devices import resolve_device
        selected, device_name = None, 'CPU'
        if device != 'cpu':
            selected = resolve_device(device_identity, device)
            if expected_driver is not None and selected['driver'] != expected_driver:
                raise InferenceFailure('GPU 驱动在检测后发生变化，请重新检测', 'config')
            device_id = selected['cuda_index'] if device == 'cuda' else selected['dxgi_index']
            device_name = selected['name']
        if device == 'cuda':
            from .gpu import cuda_driver_version, MANIFEST_PATH
            minimum=json.loads(MANIFEST_PATH.read_text())['minimum_windows_driver']
            if tuple(map(int,cuda_driver_version().split('.'))) < tuple(map(int,minimum.split('.'))):
                raise InferenceFailure('NVIDIA 驱动未达到本组件发布基线','gpu_init')
        ort = load_runtime(device, gpu_root)
        import numpy as np
        from tokenizers import Tokenizer
        tokenizer = Tokenizer.from_file(str(Path(root) / 'tokenizer.json'))
        tokenizer.enable_padding(pad_id=tokenizer.token_to_id('[PAD]') or tokenizer.token_to_id('<pad>') or 0)
        profile_dir = None
        if device != 'cpu':
            profile_dir = tempfile.TemporaryDirectory(prefix='wechat-semantic-probe-')
        session = create_session(Path(root) / 'onnx/model.onnx', device, device_id,
                                 Path(profile_dir.name) / 'probe' if profile_dir else None)
        if selected is not None:
            current = resolve_device(device_identity, device)
            ordinal = 'cuda_index' if device == 'cuda' else 'dxgi_index'
            if (current['identity'], current['luid'], current[ordinal], current['driver']) != (selected['identity'], selected['luid'], selected[ordinal], selected['driver']):
                raise InferenceFailure('GPU 身份、序号或驱动在初始化期间发生变化，请重新检测', 'config')

        def encode(texts, query=False):
            prefix = spec['query_prefix'] if query else spec['passage_prefix']
            tokens = tokenizer.encode_batch([prefix + text for text in texts])
            if any(len(t.ids) > spec['max_tokens'] for t in tokens):
                raise InferenceFailure('检索内容超过模型输入长度，请缩短搜索条件', 'input')
            arrays = {'input_ids': np.array([t.ids for t in tokens], dtype=np.int64),
                      'attention_mask': np.array([t.attention_mask for t in tokens], dtype=np.int64),
                      'token_type_ids': np.array([t.type_ids for t in tokens], dtype=np.int64)}
            outputs = session.run(None, {i.name: arrays[i.name] for i in session.get_inputs()})
            vectors = outputs[0]
            if vectors.ndim == 3:
                if spec['pooling'] == 'cls':
                    vectors = vectors[:, 0]
                else:
                    mask = arrays['attention_mask'][..., None]
                    vectors = (vectors * mask).sum(axis=1) / mask.sum(axis=1).clip(min=1)
            vectors = vectors.astype(np.float32)
            vectors /= np.linalg.norm(vectors, axis=1, keepdims=True).clip(min=1e-12)
            if vectors.shape[1] != spec['dimension'] or not np.isfinite(vectors).all():
                raise InferenceFailure('模型输出维度或数值异常', 'model')
            return vectors.tolist()

        passages = np.asarray(encode(VALIDATION_TEXTS))
        queries = np.asarray(encode(VALIDATION_QUERIES, query=True))
        if not np.allclose(np.linalg.norm(passages, axis=1), 1, atol=1e-4) or not np.allclose(np.linalg.norm(queries, axis=1), 1, atol=1e-4):
            raise InferenceFailure('固定检索样例向量未正确归一化', 'model')
        hits = (queries @ passages.T).argmax(axis=1).tolist()
        if hits != [0, 1, 2]:
            raise InferenceFailure(f'固定检索样例结果异常：{hits}', 'model')
        evidence = None
        if device != 'cpu':
            evidence = profile_evidence(session.end_profiling(), PROVIDERS[device])
            evidence.update(probe_hits=hits, finite=True, normalized=True)
            profile_dir.cleanup()
        import onnxruntime.capi.onnxruntime_pybind11_state as native
        pipe.send({'ready': True, 'device': device, 'device_id': device_id, 'device_identity': device_identity,
                   'device_name':device_name, 'runtime': ort.__version__, 'runtime_file': ort.__file__,
                   'native_file': native.__file__, 'evidence': evidence})
        while True:
            request = pipe.recv()
            if request is None:
                break
            try:
                pipe.send({'vectors': encode(request['texts'], request.get('query', False))})
            except Exception as error:
                pipe.send({'error': failure_category(error), 'diagnostic': str(error), 'diagnostic_fields': exception_fields(error)})
    except Exception as error:
        pipe.send({'error': failure_category(error), 'diagnostic': str(error), 'diagnostic_fields': exception_fields(error)})
    finally:
        pipe.close()


class LocalInference:
    def __init__(self, gpu_root=None, callback=None, dml_root=None, acceleration=None):
        self.gpu_root = gpu_root
        self.dml_root = dml_root
        self.acceleration = acceleration
        self.callback = callback or (lambda status: None)
        self.lock = threading.RLock()
        self.process = self.pipe = None
        self.key = None
        self.gpu_failed = False
        self.last_used = 0
        self.status = {'actual_device': None, 'using_fallback': False, 'reason': '', 'active': False}
        self.runtime_info={}
        self.runtime_context = None
        self.query_waiting=0
        self.priority=threading.Condition()

    def close(self):
        with self.lock:
            self._close()

    def _close(self):
        if self.process and self.process.pid is not None:
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(timeout=3)
            diagnostic_event('inference.process.exited', pid=getattr(self.process,'pid',None), exit_code=getattr(self.process,'exitcode',None))
        if self.pipe:
            self.pipe.close()
        self.process = self.pipe = self.key = None
        if self.status.get('active'):
            self.status = {**self.status, 'active': False}
            if self.acceleration is not None and self.runtime_context is not None:
                model, account = self.runtime_context
                self.acceleration.record_runtime('search', account, model, self.status)
            self.callback(self.status)
        self.runtime_context = None

    def _select(self, spec, strategy, device_id, device_identity):
        if self.acceleration is not None:
            return self.acceleration.select('search', spec, strategy, device_identity, device_id)
        if strategy not in {'auto', 'cpu', 'cuda', 'dml'}:
            raise InferenceFailure('检索设备配置无效', 'config')
        device = strategy
        if strategy == 'auto':
            device = ('cuda' if self.gpu_root else 'dml' if self.dml_root else 'cpu') if device_identity and not self.gpu_failed else 'cpu'
        runtime_root = self.gpu_root if device == 'cuda' else self.dml_root if device == 'dml' else None
        if device != 'cpu' and (sys.platform != 'win32' or not runtime_root):
            raise InferenceFailure('所选 GPU 运行组件尚未就绪', 'gpu_init')
        if device != 'cpu' and not device_identity:
            raise InferenceFailure('所选 GPU 缺少持久设备身份，请重新检测', 'config')
        return {'backend': device, 'device_id': device_id, 'device_identity': device_identity if device != 'cpu' else None,
                'runtime_root': str(runtime_root) if runtime_root else None, 'generation': 0, 'reason': ''}

    def _receive(self, cancelled, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancelled and cancelled():
                self.close()
                raise InferenceFailure('处理已暂停', 'cancelled')
            if self.pipe.poll(.1):
                try:
                    result = self.pipe.recv()
                except (EOFError, OSError):
                    raise InferenceFailure('推理进程已退出', 'process') from None
                if result.get('diagnostic_event') in {'model.file.verify.started','model.file.verify.finished'}:
                    diagnostic_event(result['diagnostic_event'], level=logging.ERROR if result.get('validation_status')=='failed' else logging.INFO,
                                     file=result.get('file'), total=result.get('total'), model=result.get('model'), validation_status=result.get('validation_status'))
                    continue
                if 'error' in result:
                    diagnostic_event('inference.process.failed', level=logging.WARNING, reason_code=result['error'], **result.get('diagnostic_fields', {}))
                    raise InferenceFailure('本地推理未完成：' + result.get('diagnostic', result['error']), result['error'])
                return result
            if not self.process.is_alive():
                raise InferenceFailure('推理进程已退出', 'process')
        raise InferenceFailure('模型响应超时', 'timeout')

    @observed('inference.encode')
    def encode(self, root, spec, texts, strategy='auto', device_id=0, query=False, cancelled=None, device_identity=None, account=''):
        queued = time.monotonic()
        with self.priority:
            if query: self.query_waiting+=1
            else:
                while self.query_waiting:
                    if cancelled and cancelled(): raise InferenceFailure('处理已暂停','cancelled')
                    self.priority.wait(.1)
        with self.lock:
            if query:
                with self.priority:
                    self.query_waiting-=1
                    self.priority.notify_all()
            if cancelled and cancelled(): raise InferenceFailure('处理已暂停','cancelled')
            # 完整模型的加载错误不按显卡故障反复重试。
            if not (Path(root)/'onnx/model.onnx').is_file() or not (Path(root)/'tokenizer.json').is_file():
                raise InferenceFailure('本地模型文件缺失，请重新下载或导入','model')
            try:
                choice = self._select(spec, strategy, device_id, device_identity)
            except AccelerationError as error:
                raise InferenceFailure(str(error), error.category) from error
            device, fallback = choice['backend'], choice.get('using_fallback', False)
            fallback_reason = choice['reason'] if fallback else ''
            diagnostic_event('inference.batch.acquired', queue_ms=(time.monotonic()-queued)*1000, actual_device=device, purpose='query' if query else 'index',
                             reason_code='configured', count=len(texts))
            for attempt in range(2):
                try:
                    key = (str(root), spec['revision'], device, choice['device_id'], choice['device_identity'],
                           choice['runtime_root'], choice['generation'])
                    if self.key != key:
                        self.close()
                        parent, child = mp.get_context('spawn').Pipe()
                        self.pipe = parent
                        self.process = mp.get_context('spawn').Process(target=inference_worker,
                            args=(child, str(root), spec, device, choice['device_id'], choice['runtime_root'], choice['device_identity'],
                                  choice.get('fingerprint', {}).get('driver')), daemon=True)
                        try:
                            self.process.start()
                        finally:
                            child.close()
                        diagnostic_event('inference.process.started', pid=getattr(self.process,'pid',None), actual_device=device, device_id=device_id)
                        ready=self._receive(cancelled)
                        diagnostic_event('inference.process.ready', actual_device=device, runtime=ready.get('runtime'), model=spec.get('id'))
                        self.runtime_info={k:ready.get(k) for k in ('device_name','runtime','runtime_file','native_file','evidence','device_id')}
                        if device != 'cpu' and self.acceleration is not None:
                            self.acceleration.validated(choice, ready['evidence'])
                        self.key = key
                    self.pipe.send({'texts': texts, 'query': query})
                    vectors = self._receive(cancelled)['vectors']
                    if cancelled and cancelled():
                        raise InferenceFailure('处理已暂停', 'cancelled')
                    diagnostic_event('inference.batch.finished', actual_device=device, count=len(vectors), using_fallback=fallback)
                    self.last_used = time.monotonic()
                    previous_status = self.status
                    if self.acceleration is not None and self.runtime_context is not None and self.runtime_context != (spec, account):
                        previous_model, previous_account = self.runtime_context
                        self.acceleration.record_runtime('search', previous_account, previous_model, {**self.status, 'active': False})
                    self.status = {**self.runtime_info,'actual_device': device, 'backend': device, 'using_fallback': fallback,
                        'device_id': self.runtime_info.get('device_id') if device != 'cpu' else None,
                        'device_identity': choice['device_identity'], 'active': True, 'account': account,
                        'cpu_participation': bool((self.runtime_info.get('evidence') or {}).get('cpu_nodes', 0)),
                        'diagnostic': fallback_reason or None, 'reason': fallback_reason or choice['reason'],
                        'strategy': strategy, 'selected_device_identity': device_identity}
                    self.runtime_context = (spec, account)
                    if self.acceleration is not None:
                        self.acceleration.record_runtime('search', account, spec, self.status)
                    # 设备未变时不逐批写入相同事件，避免快速推理淹没进度推送。
                    if self.status != previous_status:
                        self.callback(self.status)
                    return vectors
                except Exception as error:
                    self.close()
                    category = failure_category(error)
                    if self.acceleration is not None:
                        self.acceleration.failed(choice, error)
                    if strategy == 'auto' and device != 'cpu' and category in GPU_FAILURES and attempt == 0:
                        diagnostic_event('inference.cpu.fallback', level=logging.WARNING, error=error, reason_code=category)
                        self.gpu_failed, fallback, device = True, True, 'cpu'
                        fallback_reason = f'GPU 推理失败，已使用 CPU 恢复当前批次：{error}'
                        choice = {**choice, 'backend': 'cpu', 'device_id': 0, 'device_identity': None, 'runtime_root': None, 'reason': fallback_reason}
                        self.status = {'actual_device': 'switching', 'using_fallback': True, 'reason': 'GPU 推理失败，正在使用 CPU 恢复当前批次',
                                       'diagnostic': str(error), 'active': False, 'account': account}
                        self.callback(self.status)
                        continue
                    if isinstance(error, InferenceFailure): raise
                    raise InferenceFailure(str(error), category) from error
            raise InferenceFailure('CPU 恢复失败')
