"""Local-only automatic reply configuration and explicit run controls."""
import asyncio
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, field_validator

from .ai import account_name, local_only
from .chat_send import get_wechat_bridge
from ..wechat_ui_bridge import WeChatWindowBinding

router = APIRouter(prefix="/api/ai/auto-reply", dependencies=[Depends(local_only)])
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]


def get_auto_reply_service():
    from ..ai.auto_reply import get_auto_reply_service as get_service
    return get_service()


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionInput(Input):
    session_id: Name
    target_name: Name
    enabled: StrictBool = False
    group_trigger: Literal["mentions", "all"] = "mentions"

    @field_validator("session_id", "target_name")
    @classmethod
    def single_line(cls, value):
        if any(char in value for char in ("\n", "\r", "\x00")):
            raise ValueError("会话 ID 和定位名称必须为单行文本")
        return value


class ConfigInput(Input):
    account: Name
    version: int = Field(ge=0)
    profile_id: str = ""
    model_id: str = ""
    system_prompt: str = Field(default="", max_length=16000)
    reasoning_effort: str | None = None
    thinking_mode: str | None = None
    thinking_budget: int | None = Field(default=None, gt=0)
    sessions: list[SessionInput] = Field(default_factory=list)

    @field_validator("sessions")
    @classmethod
    def unique_sessions(cls, values):
        if len({item.session_id for item in values}) != len(values):
            raise ValueError("会话名单不能包含重复的会话 ID")
        return values


class BindingInput(Input):
    pid: int = Field(gt=0, strict=True)
    process_created_at: float = Field(gt=0, allow_inf_nan=False)
    hwnd: int = Field(gt=0, strict=True)


class ConfirmedInput(Input):
    account: Name
    confirmed: StrictBool

    @field_validator("confirmed")
    @classmethod
    def account_confirmed(cls, value):
        if not value:
            raise ValueError("启用前请确认数据账号与当前微信登录账号一致")
        return value


class StartInput(ConfirmedInput):
    binding: BindingInput


class StopInput(Input):
    account: Name


class ResumeInput(ConfirmedInput):
    session_id: Name


class ReviewInput(Input):
    account: Name
    attempt_id: Name
    conclusion: Literal["sent", "not_sent", "uncertain"]


@router.get("/config")
async def config(account: Name):
    return get_auto_reply_service().config(account_name(account))


@router.put("/config")
async def configure(body: ConfigInput):
    return await get_auto_reply_service().configure(account_name(body.account), body.model_dump(exclude={"account"}))


@router.get("/windows")
async def windows():
    candidates = await asyncio.to_thread(get_wechat_bridge().list_send_windows)
    return {"windows": [asdict(window) for window in candidates]}


@router.post("/start")
async def start(body: StartInput):
    return await get_auto_reply_service().start(account_name(body.account),
        WeChatWindowBinding(**body.binding.model_dump()), body.confirmed)


@router.post("/stop")
async def stop(body: StopInput):
    return await get_auto_reply_service().stop(expected_account=account_name(body.account))


@router.get("/status")
async def status(account: Name):
    return get_auto_reply_service().status(account_name(account))


@router.get("/history")
async def history(account: Name, session_id: str | None = None, status: str | None = None,
                  page: int = Query(default=1, ge=1), page_size: int = Query(default=5, ge=1, le=5)):
    return get_auto_reply_service().history(account_name(account), session_id=session_id, status=status,
                                            page=page, page_size=page_size)


@router.delete("/history")
async def delete_all_history(account: Name):
    return get_auto_reply_service().delete_history(account_name(account))


@router.delete("/history/{attempt_id}")
async def delete_history(attempt_id: Name, account: Name):
    return get_auto_reply_service().delete_history(account_name(account), attempt_id)


@router.post("/review")
async def review(body: ReviewInput):
    service = get_auto_reply_service()
    account = account_name(body.account)
    await service.review(account, body.attempt_id, body.conclusion)
    return service.status(account)


@router.post("/resume")
async def resume(body: ResumeInput):
    return await get_auto_reply_service().resume(account_name(body.account), body.session_id, body.confirmed)
