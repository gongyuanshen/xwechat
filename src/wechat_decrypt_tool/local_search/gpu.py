"""版本化 CUDA / DirectML 组件安装；不修改系统和主 Python 环境。"""
from ..ai.diagnostics import observed, event as diagnostic_event
import logging
import asyncio
import csv
from importlib import metadata
import json
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

from packaging.requirements import Requirement

from .catalog import file_hash

MANIFEST_PATH = Path(__file__).parents[1] / 'resources/local_search_gpu.json'
DML_MANIFEST_PATH = Path(__file__).parents[1] / 'resources/local_acceleration_dml.json'


class ComponentInstallationError(ValueError):
    """已存在的组件目录不完整、损坏或无法读取。"""


def validate_application_dependencies(manifest):
    """组件私有目录以外的依赖必须由应用明确提供；不导入推理包。"""
    dependencies = {}
    for value in manifest.get('dependencies_from_application', []):
        requirement = Requirement(value)
        try:
            version = metadata.version(requirement.name)
        except metadata.PackageNotFoundError as error:
            raise ComponentInstallationError(f'应用缺少组件依赖：{value}') from error
        if version not in requirement.specifier:
            raise ComponentInstallationError(f'应用组件依赖版本不兼容：{value}，实际 {version}')
        dependencies[requirement.name] = {'requirement': value, 'version': version}
    return dependencies

def cuda_driver_version():
    """驱动基线是整机安装版本；禁止把 nvidia-smi 序号当作 CUDA 设备身份。"""
    from ..local_acceleration_runtime import AccelerationError
    try:
        result = subprocess.run(['nvidia-smi', '--query-gpu=driver_version', '--format=csv,noheader'],
                                capture_output=True, text=True, timeout=4,
                                creationflags=0x08000000 if sys.platform == 'win32' else 0, check=True)
        versions = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        if len(versions) != 1:
            raise ValueError('NVIDIA 驱动版本为空或存在歧义')
        version = versions.pop()
        tuple(map(int, version.split('.')))
        return version
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise AccelerationError(f'NVIDIA 驱动基线核验失败：{error}', 'gpu_init') from error


class GPUComponent:
    def __init__(self, root, store, engine=None, backend='cuda'):
        if backend not in {'cuda', 'dml'}:
            raise ValueError(f'无效组件 backend: {backend}')
        self.root, self.store, self.engine = Path(root),store,engine
        self.backend = backend
        self.label = 'NVIDIA CUDA' if backend == 'cuda' else 'DirectML'
        # CUDA 保持旧持久化键和 SSE 事件，以兼容已有任务和入口。
        self.job_id = 'global' if backend == 'cuda' else 'dml'
        self.event_kind = 'local_search_gpu' if backend == 'cuda' else 'local_acceleration_dml'
        manifest_path = MANIFEST_PATH if backend == 'cuda' else DML_MANIFEST_PATH
        self.manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        tag=f'cp{sys.version_info.major}{sys.version_info.minor}'
        self.files=[f for f in self.manifest['files'] if tag in f['name'] or 'py3-none' in f['name']]
        self.supported=sys.platform=='win32' and platform.machine().lower() in {'amd64','x86_64'} and any(tag in f['name'] for f in self.files)
        self.destination=self.root/self.manifest['id']/tag
        self.task=None
        self.cancelled=threading.Event()
        self.application_dependencies = {}
        if self.status()['installed'] and self.engine is not None and self.backend == 'cuda':
            self.engine.gpu_root=self.destination
        diagnostic_event('gpu.component.detected', component=self.manifest['id'], backend=backend, supported=self.supported, platform=sys.platform, arch=platform.machine())

    def installed(self):
        try:
            self.destination.stat()
        except FileNotFoundError:
            return False
        except OSError as error:
            raise ComponentInstallationError(f'{self.label} 组件目录读取失败：{error}') from error
        self._validate_installation(self.destination)
        return True

    def _validate_installation(self, root):
        """下载时校验 wheel SHA-256；目录检查按 RECORD 核对解压闭包与大小。"""
        self.application_dependencies = validate_application_dependencies(self.manifest)
        try:
            saved=json.loads((root/'installed.json').read_text(encoding='utf-8'))
            if not isinstance(saved, dict) or saved.get('id') != self.manifest['id'] or saved.get('files') != self.manifest['files']:
                raise ValueError('installed.json 与固定组件清单不一致')
            recorded = set()
            for file in self.files:
                distribution, version = file['name'].split('-')[:2]
                record = root / f'{distribution}-{version}.dist-info' / 'RECORD'
                with record.open(encoding='utf-8', newline='') as source:
                    for name, digest, size in csv.reader(source):
                        path = (root / name).resolve()
                        if not path.is_relative_to(root.resolve()) or not path.is_file():
                            raise ValueError(f'组件文件缺失或路径无效：{name}')
                        if size and path.stat().st_size != int(size):
                            raise ValueError(f'组件文件大小不一致：{name}')
                        recorded.add(name)
            required = {'onnxruntime/__init__.py', 'onnxruntime/capi/onnxruntime_pybind11_state.pyd',
                        'onnxruntime/capi/onnxruntime.dll', 'onnxruntime/capi/onnxruntime_providers_shared.dll',
                        'onnxruntime/capi/DirectML.dll' if self.backend == 'dml' else
                        'onnxruntime/capi/onnxruntime_providers_cuda.dll'}
            missing = required - recorded
            if missing:
                raise ValueError(f'组件缺少运行文件：{", ".join(sorted(missing))}')
        except (OSError, ValueError, csv.Error) as error:
            raise ComponentInstallationError(f'{self.label} 组件损坏或无法读取：{type(error).__name__}: {error}') from error

    def status(self):
        error = ''
        try:
            installed = self.installed()
            installation_status = 'installed' if installed else 'missing'
        except ComponentInstallationError as failure:
            installed, installation_status, error = False, 'corrupt', str(failure)
        return {'installed':installed,'installation_status':installation_status,'backend':self.backend,
                'supported':self.supported,'platform':sys.platform,'error':error,
                'reason': error or ('' if self.supported else f'当前系统不支持此 {self.label} 组件，仍可使用 CPU'),
                'application_dependencies': self.application_dependencies,
                'version':self.manifest['id'],
                'size':sum(f['size'] for f in self.files),'job':self.store.get('gpu_component',self.job_id)}

    def update(self, **values):
        job={**(self.store.get('gpu_component',self.job_id) or {}),**values,'backend':self.backend,'updated':time.time()}
        self.store.put('gpu_component',job,id=self.job_id)
        self.store.event('',self.event_kind,job, unique_key=self.event_kind, replace=True)

    @observed('gpu.start')
    async def start(self, source=None):
        if not self.supported: raise ValueError(f'当前系统或 Python 架构不支持此 {self.label} 组件')
        if self.task and not self.task.done(): return self.status()
        if self.installed(): return self.status()
        self.cancelled.clear()
        self.update(status='queued',stage='queued',bytes=0,total=sum(f['size'] for f in self.files),error='',started=time.time(),resume_on_start=False)
        self.task=asyncio.create_task(self.run(Path(source).resolve() if source else None))
        return self.status()

    @observed('gpu.run', execution=True)
    async def run(self, source):
        import httpx
        cache=self.root/'downloads'
        completed=0
        installation=None
        try:
            cache.mkdir(parents=True,exist_ok=True)
            self.update(application_dependencies=validate_application_dependencies(self.manifest))
            async with httpx.AsyncClient(follow_redirects=True,timeout=30) as client:
                for f in self.files:
                    diagnostic_event('gpu.file.started', file=f['name'], total=f['size'])
                    path=cache/f['name']
                    if source:
                        original=source/f['name']
                        if not original.is_file(): raise ValueError(f'离线组件文件不完整：{f["name"]}')
                        if original.resolve()!=path.resolve():
                            from .downloads import copy_import_file
                            await copy_import_file(original,path)
                    elif not path.is_file() or path.stat().st_size!=f['size']:
                        for attempt in range(1,6):
                            size=path.stat().st_size if path.exists() else 0
                            diagnostic_event('gpu.file.resume', file=f['name'], bytes=size, attempt=attempt)
                            self.update(status='running',stage='downloading',attempt=attempt,bytes=completed+size)
                            try:
                                async with client.stream('GET',f['url'],headers={'Range':f'bytes={size}-'} if size else {}) as r:
                                    r.raise_for_status()
                                    if size and r.status_code!=206: size=0
                                    with path.open('ab' if size else 'wb') as output:
                                        sent=time.monotonic()
                                        log_sent=sent
                                        async for data in r.aiter_bytes(256*1024):
                                            output.write(data); size+=len(data)
                                            if time.monotonic()-sent>.3:
                                                self.update(bytes=completed+size)
                                                sent=time.monotonic()
                                            if time.monotonic()-log_sent>=5:
                                                diagnostic_event('gpu.file.progress', file=f['name'], bytes=size, total=f['size'])
                                                log_sent=time.monotonic()
                                break
                            except httpx.HTTPError as error:
                                diagnostic_event('gpu.file.failed', level=logging.WARNING, error=error, attempt=attempt, http_status=getattr(getattr(error,'response',None),'status_code',None))
                                if attempt==5: raise
                                wait=min(30,2**attempt)
                                diagnostic_event('gpu.file.retry', level=logging.WARNING, attempt=attempt+1, wait_seconds=wait)
                                self.update(stage='retry_wait',next_retry=time.time()+wait)
                                await asyncio.sleep(wait)
                    self.update(stage='verifying')
                    if path.stat().st_size!=f['size'] or await asyncio.to_thread(file_hash,path)!=f['sha256']:
                        path.unlink(missing_ok=True)
                        raise ValueError(f'{self.label} 组件校验失败，已移除损坏缓存文件：{f["name"]}')
                    completed+=f['size']
                    diagnostic_event('gpu.file.verified', file=f['name'], bytes=f['size'], validation_status='success')
            self.update(status='running',stage='installing',bytes=completed)
            installation=asyncio.create_task(asyncio.to_thread(self.install,cache))
            await asyncio.shield(installation)
            if self.engine is not None and self.backend == 'cuda':
                self.engine.gpu_root=self.destination
            self.update(status='done',stage='done')
        except asyncio.CancelledError:
            self.cancelled.set()
            if installation: await asyncio.gather(installation,return_exceptions=True)
            self.update(status='paused',stage='paused')
            raise
        except Exception as e:
            diagnostic_event('gpu.failed', level=logging.ERROR, error=e)
            self.update(status='error',error=f'{type(e).__name__}: {e}')

    @observed('gpu.install')
    def install(self, cache):
        if self.destination.exists():
            raise FileExistsError(f'组件版本目录已存在，不能覆盖可能正在使用的 DLL：{self.destination}')
        self.destination.parent.mkdir(parents=True,exist_ok=True)
        stage=Path(tempfile.mkdtemp(prefix=self.destination.name+'.staging-',dir=self.destination.parent))
        try:
            for f in self.files:
                if self.cancelled.is_set(): raise ValueError('组件安装已暂停')
                diagnostic_event('gpu.package.install.started', file=f['name'])
                with zipfile.ZipFile(cache/f['name']) as archive:
                    for member in archive.infolist():
                        target=(stage/member.filename).resolve()
                        if not target.is_relative_to(stage.resolve()) or (member.external_attr>>16)&0o170000==0o120000:
                            raise ValueError('组件包含不安全的文件路径')
                        if self.cancelled.is_set(): raise ValueError('组件安装已暂停')
                        archive.extract(member,stage)
                diagnostic_event('gpu.package.install.finished', file=f['name'])
            (stage/'installed.json').write_text(json.dumps(self.manifest),encoding='utf-8')
            self._validate_installation(stage)
            if self.cancelled.is_set(): raise ValueError('组件安装已暂停')
            # 新版本发布后由调用方在下一工作单元采用；现有 worker 和旧目录保留。
            stage.rename(self.destination)
        finally:
            if stage.exists():
                if not stage.resolve().is_relative_to(self.root.resolve()): raise ValueError('无效组件 staging 目录')
                shutil.rmtree(stage)

    @observed('gpu.pause')
    async def pause(self):
        self.cancelled.set()
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)
