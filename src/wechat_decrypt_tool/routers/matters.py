from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .ai import local_only
from ..application_accounts import application_account_name as account_name
from .chat import get_chat_messages_around
from .library import Source, validate_message_source
from ..ai.service import get_ai_service
from ..matter_service import MatterService
from ..account_workers import account_to_thread
from ..chat_helpers import _resolve_account_dir
from ..snapshot_registry import account_work

def require_account(account: str):
    if not account.strip():
        raise HTTPException(422, '请选择明确的微信账号')


router = APIRouter(prefix='/api/matters', dependencies=[Depends(local_only), Depends(require_account)])


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Create(Input):
    title: str = Field(min_length=1, max_length=200)
    source: Source
    due_at: int | None = Field(default=None, ge=0)
    reminder_enabled: bool = False

    @field_validator('title')
    @classmethod
    def title_not_blank(cls, value):
        if not value.strip():
            raise ValueError('事项标题不能为空')
        return value.strip()

    @model_validator(mode='after')
    def reminder_needs_deadline(self):
        if self.reminder_enabled and self.due_at is None:
            raise ValueError('提醒必须有用户确认的期限')
        return self


class Edit(Input):
    revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    status: Literal['todo', 'in_progress', 'done'] | None = None
    reminder_enabled: bool | None = None

    @model_validator(mode='after')
    def nonempty(self):
        if len(self.model_fields_set) == 1 or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError('请提供要更新的事项内容')
        if self.title is not None:
            self.title = Create.title_not_blank(self.title)
        return self


class Reschedule(Input):
    revision: int = Field(ge=1)
    due_at: int = Field(ge=0)
    source: Source
    confirmed: Literal[True]


def service():
    return MatterService(get_ai_service(), account_name)


async def read_source(request, account, source):
    account_dir = _resolve_account_dir(account)
    with account_work(account_dir):
        await account_to_thread(account_dir, validate_message_source, account, source)
        evidence = await get_chat_messages_around(request=request, username=source.username, anchor_id=source.anchor,
                                                  account=account, before=0, after=0, source=None)
        messages = evidence['messages']
        if (evidence['account'] != account or evidence['username'] != source.username or len(messages) != 1
                or evidence['anchorIndex'] != 0 or messages[0]['id'] != evidence['anchorId']
                or evidence['anchorId'].casefold() != source.anchor.casefold()):
            raise HTTPException(422, '消息来源与请求不一致')
        message = messages[0]
        result = {'username': source.username, 'anchor': evidence['anchorId'], 'text': message['content'],
                  'sender': message['senderUsername'], 'create_time': message['createTime'], 'render_type': message['renderType']}
        if any(value is not None and value != result[key] for key, value in source.model_dump().items() if key not in ('anchor', 'username')):
            raise HTTPException(409, '原消息已变化，请重新查看原文后确认')
        return result


@router.post('/source')
async def preview_source(body: Source, account: str, request: Request):
    return await read_source(request, account_name(account), body)


@router.get('')
def list_matters(account: str):
    return service().list(account_name(account))


@router.post('')
async def create_matter(body: Create, account: str, request: Request):
    owner = account_name(account)
    with account_work(_resolve_account_dir(owner)):
        source = await read_source(request, owner, body.source)
        return service().create(owner, {**body.model_dump(), 'source': source})


@router.patch('/{id}')
def update_matter(id: str, body: Edit, account: str):
    return service().update(account, id, body.revision, body.model_dump(exclude_unset=True, exclude={'revision'}))


@router.post('/{id}/reschedule')
async def reschedule_matter(id: str, body: Reschedule, account: str, request: Request):
    owner, svc = account_name(account), service()
    with svc.store.connection() as db:
        svc.get(db, id, owner)
    with account_work(_resolve_account_dir(owner)):
        source = await read_source(request, owner, body.source)
        return svc.update(owner, id, body.revision, {'due_at': body.due_at}, source)
