"""Local-only capability, component and non-private model validation endpoints."""
import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .ai import account_name, local_only
from .local_search import events
from ..local_acceleration_runtime import AccelerationError
from ..local_search.catalog import model_spec
from ..local_acceleration import begin_validation, start_component
from ..local_search.service import get_local_search


router = APIRouter(prefix='/api/ai/local-acceleration', dependencies=[Depends(local_only)])
router.add_api_route('/events', events, methods=['GET'])


class ValidationBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    feature: Literal['search', 'ocr']
    backend: Literal['cuda', 'dml']
    device_identity: str = Field(min_length=1, max_length=512)
    account: str | None = None


class OcrSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    device: Literal['auto', 'cpu', 'dml']
    device_identity: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode='after')
    def identity(self):
        if self.device == 'dml' and (self.device_identity is None or not self.device_identity.strip()):
            raise ValueError('请选择具体 AMD 显卡')
        if self.device != 'dml' and self.device_identity is not None:
            raise ValueError('自动或 CPU 模式不能指定显卡')
        return self


class ImportBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(min_length=1)


def capability_status(service, account=None):
    event_cursor = service.store.latest_event_id()
    from ..local_ocr import ocr_model_spec
    from ..voice_transcription import get_voice_transcription_service
    from ..ai.insight_local_models import SPEC as LAYA
    from ..ai.laya_runtime import get_laya_runtime_status

    manager = service.acceleration
    cfg = service.config(account) if account else {'device': 'auto', 'device_identity': None, 'model': None}
    search_model = model_spec(cfg['model']) if cfg['model'] else None
    ocr_model = ocr_model_spec()
    features = []
    for feature, name, model, settings, owner, backends in (
        ('search', '本地检索', search_model, cfg, account, ['cpu', 'cuda', 'dml']),
        ('ocr', '图片与扫描 PDF OCR', ocr_model, manager.ocr_settings(), '', ['cpu', 'dml']),
    ):
        features.append({'id': feature, 'name': name,
                         'model': {'id': model['id'], 'name': model.get('name', 'PP-OCRv6')} if model else None,
                         'supported_backends': backends, 'settings': settings,
                         'runtime': manager.runtime_status(feature, owner, model) if model else {'actual_device': None},
                         'validations': manager.validations(feature, model) if model else []})
    voice = get_voice_transcription_service().status()
    features.append({'id': 'voice', 'name': '语音转文字',
                     'model': {'id': voice['model'], 'name': voice['model']} if voice['model'] else None,
                     'supported_backends': voice['supportedDevices'],
                     'settings': {'device': voice['requestedDevice']}, 'validations': [],
                     'runtime': {'actual_device': voice['activeDevice'] if voice['modelLoaded'] else None,
                                 'active': voice['modelLoaded'], 'using_fallback': voice['usingFallback'],
                                 'reason': voice['fallbackReason'] or voice['reason']},
                     'amd_reason': '当前后端未接入 AMD'})
    features.append({'id': 'laya', 'name': 'Laya 本地分析', 'model': {'id': LAYA['id'], 'name': LAYA['name']},
                     'supported_backends': ['cpu'], 'settings': {'device': 'cpu'}, 'validations': [],
                     'runtime': get_laya_runtime_status()})
    return {'hardware': manager.hardware, 'components': manager.component_statuses(), 'features': features,
            'jobs': list(manager.jobs.values()), 'event_cursor': event_cursor}


@router.get('/status')
def status(account: str | None = None):
    return capability_status(get_local_search(), account_name(account) if account else None)


@router.post('/recheck')
async def recheck():
    manager = get_local_search().acceleration
    try:
        manager.start_detection(reset_failures=True)
    except AccelerationError as error:
        raise HTTPException(400, str(error)) from error
    # Publish the detecting state before returning; enumeration remains asynchronous.
    await asyncio.sleep(0)
    return manager.hardware


@router.post('/validate')
async def validate(body: ValidationBody):
    try:
        return begin_validation(get_local_search(), body.feature, body.backend, body.device_identity,
                                account_name(body.account) if body.account else None)
    except (ValueError, AccelerationError) as error:
        raise HTTPException(400, str(error)) from error


@router.post('/validate/{job_id}/cancel')
async def cancel_validation(job_id: str):
    try:
        return get_local_search().acceleration.cancel_validation(job_id)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error


@router.put('/ocr/settings')
def ocr_settings(body: OcrSettings):
    return get_local_search().acceleration.configure_ocr(body.device, body.device_identity)


@router.post('/components/{backend}/{action}')
async def component_action(backend: Literal['cuda', 'dml'], action: Literal['download', 'pause', 'import'],
                           body: ImportBody | None = None, account: str | None = None):
    service = get_local_search()
    manager, owner = service.acceleration, account_name(account) if account else None
    component = manager.components[backend]
    try:
        if manager.stopping:
            raise AccelerationError('加速服务正在关闭', 'cancelled')
        if action == 'pause':
            await component.pause()
        else:
            if action == 'import' and body is None:
                raise ValueError('请选择离线组件目录')
            await start_component(service, backend, body.path if action == 'import' else None, owner)
        return (await asyncio.to_thread(manager.component_statuses, True))[backend]
    except (ValueError, AccelerationError) as error:
        raise HTTPException(400, str(error)) from error
