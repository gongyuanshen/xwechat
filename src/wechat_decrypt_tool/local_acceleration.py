"""Machine-wide capabilities; feature workers retain their own execution/commit boundaries."""
import asyncio
import hashlib
from functools import partial
import json
import sys
import threading
import time
import uuid

from .gpu_devices import enumerate_devices
from .local_search.catalog import model_dir, model_spec
from .local_acceleration_runtime import AccelerationError, GPU_FAILURES, failure_category


SUPPORTED = {'search': ('cuda', 'dml'), 'ocr': ('dml',)}


def model_key(model):
    return hashlib.sha256(json.dumps(model, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


class LocalAcceleration:
    def __init__(self, store, components):
        self.store, self.components = store, components
        self.hardware = {'status': 'unverified', 'generation': 0, 'devices': [], 'error': ''}
        self.lock = threading.RLock()
        self.runtime = {}
        self.tasks, self.cancellations, self.jobs = {}, {}, {}
        self.detection_task = None
        self._components = None
        self.stopping = False

    def component_statuses(self, refresh=False):
        # Validate the installed closure at detection/install boundaries, not every vector batch.
        with self.lock:
            jobs = {backend: self.store.get('gpu_component', 'global' if backend == 'cuda' else backend)
                    for backend in self.components}
            if refresh or self._components is None:
                self._components = {backend: component.status() for backend, component in self.components.items()}
            else:
                for backend, job in jobs.items():
                    if job and job['status'] == 'done' and job != self._components[backend].get('job'):
                        self._components[backend] = self.components[backend].status()
            return {backend: {**value, 'job': jobs[backend]}
                    for backend, value in self._components.items()}

    def event(self, kind, value, account=''):
        self.store.event(account, kind, value, unique_key=f'{kind}:{account}', replace=True)

    async def detect(self, reset_failures=False):
        with self.lock:
            generation = self.hardware['generation'] + 1
            self.hardware = {'status': 'detecting', 'generation': generation, 'devices': [], 'error': ''}
        self.event('local_acceleration_hardware', self.hardware)
        try:
            await asyncio.to_thread(self.component_statuses, True)
            if sys.platform != 'win32':
                result = {'status': 'unsupported', 'devices': [], 'error': '新增加速仅支持 Windows x64'}
            else:
                devices = await asyncio.to_thread(enumerate_devices)
                result = {'status': 'ready', 'devices': devices, 'error': ''}
        except Exception as error:
            result = {'status': 'error', 'devices': [], 'error': str(error)}
        with self.lock:
            if generation != self.hardware['generation']:
                return self.hardware
            if reset_failures and result['status'] == 'ready':
                faults = self.store.list('acceleration_fault')
                for record in faults:
                    self.store.delete('acceleration_fault', record['id'])
                for record in self.store.list('acceleration_validation'):
                    faulted = any(record['backend'] == fault['backend'] and
                                  record['identity'].casefold() == fault['device_identity'].casefold() for fault in faults)
                    if record['status'] != 'passed' or faulted:
                        self.store.delete('acceleration_validation', record['id'])
            self.hardware = {**result, 'generation': generation, 'checked_at': time.time()}
        self.event('local_acceleration_hardware', self.hardware)
        return self.hardware

    def start_detection(self, reset_failures=False):
        if self.stopping:
            raise AccelerationError('加速服务正在关闭', 'cancelled')
        if self.detection_task and not self.detection_task.done():
            return self.hardware
        self.detection_task = asyncio.create_task(self.detect(reset_failures))
        return self.hardware

    @staticmethod
    def fault_key(backend, identity):
        return hashlib.sha256((backend + ':' + identity.casefold()).encode()).hexdigest()

    def _choice(self, feature, model, backend, device):
        component = self.components[backend]
        fingerprint = {'feature': feature, 'model': model_key(model), 'backend': backend,
                       'identity': device['identity'], 'driver': device['driver'],
                       'component': component.manifest['id'], 'abi': f'cp{sys.version_info.major}{sys.version_info.minor}',
                       'probe_version': 1}
        key = model_key(fingerprint)
        validation = self.store.get('acceleration_validation', key)
        return {'backend': backend, 'device_identity': device['identity'], 'device_name': device['name'],
                'device_id': device['dxgi_index'] if backend == 'dml' else device['cuda_index'],
                'runtime_root': str(component.destination), 'validation_key': key, 'fingerprint': fingerprint,
                'generation': self.hardware['generation'], 'validated': bool(validation and validation['status'] == 'passed'),
                'validation': validation or {'status': 'unverified'}, 'reason': ''}

    def select(self, feature, model, strategy, identity=None, legacy_id=None, for_validation=False):
        if feature not in SUPPORTED or strategy not in ('auto', 'cpu', *SUPPORTED[feature]):
            raise AccelerationError('该功能不支持所选加速方式', 'config')
        cpu = {'backend': 'cpu', 'device_identity': None, 'device_name': 'CPU', 'device_id': 0,
               'runtime_root': None, 'validated': True, 'reason': '', 'generation': self.hardware['generation']}
        if strategy == 'cpu':
            return cpu
        if self.hardware['status'] != 'ready':
            reason = self.hardware['error'] or '显卡检测尚未完成'
            if strategy != 'auto':
                raise AccelerationError(reason, 'gpu_init')
            return {**cpu, 'reason': reason}
        candidates, reasons, device_failed = [], [], False
        components = self.component_statuses()
        for backend in SUPPORTED[feature]:
            if strategy != 'auto' and backend != strategy:
                continue
            component = components[backend]
            if not component['supported'] or not component['installed']:
                reasons.append(component.get('error') or component.get('reason') or f'{backend.upper()} 组件尚未安装')
                continue
            for device in self.hardware['devices']:
                if not device['eligible'] or device['vendor'] != ('nvidia' if backend == 'cuda' else 'amd'):
                    continue
                if identity and identity.casefold() != device['identity'].casefold():
                    continue
                if not identity and strategy == 'cuda' and legacy_id is not None and device['cuda_index'] != legacy_id:
                    continue
                if backend == 'cuda' and device['cuda_index'] is None:
                    reasons.append(device.get('cuda_error') or 'CUDA 设备序号未能与实例身份对应')
                    continue
                fault = self.store.get('acceleration_fault', self.fault_key(backend, device['identity']))
                if fault:
                    reasons.append(fault['reason'])
                    device_failed = True
                    continue
                choice = self._choice(feature, model, backend, device)
                if choice['validation']['status'] == 'failed' and not for_validation:
                    reasons.append(choice['validation']['reason'])
                    continue
                candidates.append((not choice['validated'], backend != 'cuda', -device['dedicated_memory_bytes'], device['identity'], choice))
        if candidates:
            return min(candidates, key=lambda c: c[:4])[-1]
        reason = '；'.join(dict.fromkeys(reasons)) or '未找到所选可用设备，请重新检测或选择设备'
        if strategy != 'auto':
            raise AccelerationError(reason, 'gpu_init')
        return {**cpu, 'reason': reason, 'using_fallback': device_failed}

    def validated(self, choice, evidence):
        if choice['backend'] == 'cpu':
            return
        if evidence.get('gpu_nodes', 0) <= 0:
            raise AccelerationError('缺少实际 GPU 执行证据', 'gpu_init')
        with self.lock:
            if choice['generation'] != self.hardware['generation']:
                raise AccelerationError('设备检测已变化，请重新验证', 'cancelled')
            value = {**choice['fingerprint'], 'status': 'passed', 'evidence': evidence, 'checked_at': time.time(), 'reason': ''}
            self.store.put('acceleration_validation', value, id=choice['validation_key'])
        self.event('local_acceleration_validation', value)

    def failed(self, choice, error):
        if choice['backend'] == 'cpu' or failure_category(error) not in GPU_FAILURES:
            return
        with self.lock:
            if choice['generation'] != self.hardware['generation']:
                return
            value = {'backend': choice['backend'], 'device_identity': choice['device_identity'],
                     'reason': str(error), 'category': failure_category(error), 'checked_at': time.time()}
            self.store.put('acceleration_fault', value, id=self.fault_key(choice['backend'], choice['device_identity']))
        self.event('local_acceleration_fault', value)

    def ocr_settings(self):
        value = self.store.get('acceleration_config', 'ocr')
        if value is None:
            return {'device': 'auto', 'device_identity': None, 'revision': 0}
        self.validate_ocr_settings(value)
        return value

    @staticmethod
    def validate_ocr_settings(value):
        if not isinstance(value, dict) or value.get('device') not in ('auto', 'cpu', 'dml'):
            raise ValueError('OCR 设备配置无效')
        identity = value.get('device_identity')
        if value['device'] == 'dml' and (not isinstance(identity, str) or not identity.strip()):
            raise ValueError('OCR 设备身份配置无效')
        if value['device'] != 'dml' and identity is not None:
            raise ValueError('OCR 设备身份配置无效')
        if type(value.get('revision')) is not int or value['revision'] < 0:
            raise ValueError('OCR 配置版本无效')

    def configure_ocr(self, device, device_identity=None):
        if device not in ('auto', 'cpu', 'dml') or (device_identity is not None and not isinstance(device_identity, str)):
            raise ValueError('OCR 设备配置无效')
        old = self.ocr_settings()
        value = {'device': device, 'device_identity': device_identity, 'revision': old['revision'] + 1}
        self.validate_ocr_settings(value)
        self.store.put('acceleration_config', value, id='ocr')
        self.event('local_acceleration_settings', value)
        return value

    def record_runtime(self, feature, account, model, status):
        value = {**status, 'feature': feature, 'model_key': model_key(model)}
        value.pop('updated', None)
        with self.lock:
            previous = self.runtime.get((feature, account))
            if previous is not None and value == {key: item for key, item in previous.items() if key != 'updated'}:
                return
            value['updated'] = time.time()
            self.event('local_acceleration_runtime', value, account)
            self.runtime[(feature, account)] = value

    def runtime_status(self, feature, account, model):
        with self.lock:
            value = self.runtime.get((feature, account))
        if value and value['model_key'] == model_key(model):
            if feature == 'ocr':
                if value.get('settings_revision') == self.ocr_settings()['revision']:
                    return value
            elif feature == 'search':
                saved = self.store.get('config', account)
                cfg = saved if saved is not None else {}
                if (value.get('strategy'), value.get('selected_device_identity')) == (cfg.get('device', 'auto'), cfg.get('device_identity')):
                    return value
        return {'actual_device': None, 'reason': '', 'using_fallback': False}

    def validations(self, feature, model):
        values = []
        for backend in SUPPORTED[feature]:
            for device in self.hardware['devices']:
                if not device['eligible'] or device['vendor'] != ('nvidia' if backend == 'cuda' else 'amd'):
                    continue
                choice = self._choice(feature, model, backend, device)
                fault = self.store.get('acceleration_fault', self.fault_key(backend, device['identity']))
                validation = choice['validation']
                reason = device.get('cuda_error') if backend == 'cuda' else None
                values.append({'backend': backend, 'device_identity': device['identity'],
                               'status': 'failed' if fault or reason else validation['status'],
                               'reason': fault['reason'] if fault else reason or validation.get('reason', ''),
                               'evidence': validation.get('evidence')})
        return values

    def start_validation(self, feature, model, backend, identity, operation):
        if self.stopping:
            raise AccelerationError('加速服务正在关闭', 'cancelled')
        choice = self.select(feature, model, backend, identity, for_validation=True)
        for old_id, job in list(self.jobs.items()):
            if job['validation_key'] == choice['validation_key']:
                if job['status'] == 'validating':
                    if job['generation'] == choice['generation']:
                        return job
                else:
                    del self.jobs[old_id]
        job_id = uuid.uuid4().hex
        cancelled = threading.Event()
        job = {'id': job_id, 'feature': feature, 'model': model['id'], 'backend': backend,
               'device_identity': identity, 'validation_key': choice['validation_key'],
               'generation': choice['generation'], 'status': 'validating', 'reason': ''}
        self.jobs[job_id], self.cancellations[job_id] = job, cancelled

        async def perform():
            try:
                evidence = await asyncio.to_thread(operation, choice, cancelled.is_set)
                if cancelled.is_set():
                    raise AccelerationError('验证已取消', 'cancelled')
                self.validated(choice, evidence)
                job.update(status='passed', evidence=evidence)
            except Exception as error:
                category = failure_category(error)
                job.update(status='cancelled' if category == 'cancelled' else 'failed', reason=str(error))
                self.failed(choice, error)
                if category != 'cancelled' and choice['generation'] == self.hardware['generation']:
                    self.store.put('acceleration_validation', {**choice['fingerprint'], 'status': 'failed', 'reason': str(error)}, id=choice['validation_key'])
            finally:
                try:
                    self.event('local_acceleration_validation_job', job)
                finally:
                    self.cancellations.pop(job_id)
                    self.tasks.pop(job_id)
                    matching = [id for id, value in self.jobs.items() if value['validation_key'] == choice['validation_key']]
                    for old_id in matching[:-1]:
                        if self.jobs[old_id]['status'] != 'validating':
                            del self.jobs[old_id]
        self.tasks[job_id] = asyncio.create_task(perform())
        self.event('local_acceleration_validation_job', job)
        return job

    def cancel_validation(self, job_id):
        if job_id not in self.jobs:
            raise ValueError('验证任务不存在')
        if job_id in self.cancellations:
            self.cancellations[job_id].set()
        return self.jobs[job_id]

    async def stop(self):
        self.stopping = True
        for cancelled in self.cancellations.values():
            cancelled.set()
        pending = list(self.tasks.values())
        if self.detection_task:
            pending.append(self.detection_task)
        if pending:
            await asyncio.gather(*pending)


def validate_search(root, spec, choice, cancelled):
    import numpy as np
    from .local_search.inference import LocalInference, VALIDATION_TEXTS, VALIDATION_QUERIES
    from .gpu_devices import resolve_device
    def check_driver():
        if 'fingerprint' in choice:
            device = resolve_device(choice['device_identity'], choice['backend'])
            if device['driver'] != choice['fingerprint']['driver']:
                raise AccelerationError('显卡驱动已变化，请重新检测后验证', 'config')
    check_driver()
    engine = LocalInference(gpu_root=choice['runtime_root'] if choice['backend'] == 'cuda' else None,
                            dml_root=choice['runtime_root'] if choice['backend'] == 'dml' else None)
    texts, queries = VALIDATION_TEXTS, VALIDATION_QUERIES
    results = {}
    try:
        for backend in (choice['backend'], 'cpu'):
            passages = np.asarray(engine.encode(root, spec, texts, backend, cancelled=cancelled,
                                                device_identity=choice['device_identity']), dtype=np.float32)
            queries_encoded = np.asarray(engine.encode(root, spec, queries, backend, query=True, cancelled=cancelled,
                                                       device_identity=choice['device_identity']), dtype=np.float32)
            results[backend] = (passages, queries_encoded)
            if backend != 'cpu':
                evidence = dict(engine.status['evidence'])
        gpu, cpu = results[choice['backend']], results['cpu']
        delta = max(float(np.max(np.abs(a - b))) for a, b in zip(gpu, cpu))
        cosine = min(float(np.min(np.sum(a * b, axis=1))) for a, b in zip(gpu, cpu))
        hits = [(queries @ passages.T).argmax(axis=1).tolist()
                for passages, queries in (gpu, cpu, (cpu[0], gpu[1]), (gpu[0], cpu[1]))]
        if delta > .001 or cosine < .99995 or any(value != [0, 1, 2] for value in hits):
            raise AccelerationError(f'检索 GPU 与 CPU 基线不一致：max_abs={delta}, cosine={cosine}, hits={hits}', 'model')
        check_driver()
        return {**evidence, 'max_abs_difference': delta, 'minimum_cosine': cosine, 'hits': hits}
    finally:
        engine.close()


def begin_validation(service, feature, backend, identity, account=None):
    if feature == 'ocr':
        from .local_ocr import ocr_model_spec, validate_ocr
        spec, operation = ocr_model_spec(), validate_ocr
    else:
        if not account:
            raise ValueError('检索模型验证需要选择账号')
        cfg = service.config(account)
        if not cfg['model'] or not service.downloads.available(cfg['model']):
            raise ValueError('请先选择并安装当前检索模型')
        spec = model_spec(cfg['model'])
        operation = partial(validate_search, model_dir(service.downloads.root, spec['id']), spec)
    return service.acceleration.start_validation(feature, spec, backend, identity, operation)


async def verify_installation(service, backend, task, account):
    manager = service.acceleration
    try:
        await task
    except asyncio.CancelledError:
        if task.cancelled():
            return  # An explicit pause already persisted its component state.
        raise
    components = await asyncio.to_thread(manager.component_statuses, True)
    if not components[backend]['installed'] or manager.stopping:
        return  # Download's durable error/paused state is already exposed by the component.
    if manager.detection_task:
        await manager.detection_task
    if manager.stopping:
        return
    models = ['ocr'] if backend == 'dml' else []
    if account:
        cfg = service.config(account)
        if cfg['model'] and service.downloads.available(cfg['model']):
            models.append('search')
    for feature in models:
        for device in manager.hardware['devices']:
            if device['eligible'] and device['vendor'] == ('amd' if backend == 'dml' else 'nvidia'):
                try:
                    begin_validation(service, feature, backend, device['identity'], account)
                except (ValueError, AccelerationError) as error:
                    manager.event('local_acceleration_install_validation', {
                        'feature': feature, 'backend': backend, 'status': 'failed', 'reason': str(error)})


async def start_component(service, backend, source=None, account=None):
    manager = service.acceleration
    if manager.stopping:
        raise AccelerationError('加速服务正在关闭', 'cancelled')
    component = manager.components[backend]
    previous = component.task
    await component.start(source)
    task = component.task
    if task is None or task.done():
        return
    if task is not previous:
        component.update(validation_account=account)
    key = ('install', backend, task)
    if key in manager.tasks:
        return

    async def observe():
        try:
            await verify_installation(service, backend, task, account)
        finally:
            manager.tasks.pop(key)
    manager.tasks[key] = asyncio.create_task(observe())


def get_local_acceleration():
    from .local_search.service import get_local_search
    return get_local_search().acceleration
