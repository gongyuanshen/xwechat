from __future__ import annotations

import asyncio
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException
from starlette.requests import Request

from ..access_control import require_local_request, require_strict_local_request
from ..logging_config import get_log_file_path, get_logger
from ..network_access import get_lan_access_host
from ..runtime_settings import (
    LAN_BACKEND_HOST,
    LOOPBACK_BACKEND_HOST,
    default_backend_host,
    read_effective_backend_host,
    read_effective_mcp_token,
    read_effective_backend_port,
    reset_mcp_token,
    write_backend_host_env_file,
    write_backend_host_setting,
    write_backend_port_env_file,
    write_backend_port_setting,
    write_mcp_token_env_file,
)
from ..server_lifecycle import (
    DEFAULT_RESTART_WAIT_TIMEOUT_S,
    request_graceful_shutdown,
)


router = APIRouter()
logger = get_logger(__name__)

DEFAULT_BACKEND_PORT = 10392
_PORT_CHANGE_IN_PROGRESS = False


def _format_host_for_url(host: str) -> str:
    h = str(host or "").strip() or "127.0.0.1"
    if ":" in h and not (h.startswith("[") and h.endswith("]")):
        return f"[{h}]"
    return h


def _get_backend_bind_host() -> str:
    host, _ = read_effective_backend_host(default=default_backend_host())
    return host


def _get_backend_access_host() -> str:
    host = _get_backend_bind_host()
    if host in {"0.0.0.0", "::"}:
        return "127.0.0.1"
    return host


def _get_mcp_access_host(bind_host: str | None = None) -> str:
    host = str(bind_host or _get_backend_bind_host() or "").strip()
    if host in {LAN_BACKEND_HOST, "::"}:
        return get_lan_access_host(default=LOOPBACK_BACKEND_HOST)
    return host or LOOPBACK_BACKEND_HOST


def _get_mcp_access_urls(port: int, bind_host: str | None = None) -> dict:
    access_host = _get_mcp_access_host(bind_host)
    origin = f"http://{_format_host_for_url(access_host)}:{int(port)}"
    return {
        "access_host": access_host,
        "accessHost": access_host,
        "mcp_endpoint": f"{origin}/mcp",
        "mcpEndpoint": f"{origin}/mcp",
        "skill_bundle_url": f"{origin}/mcp/skill/bundle",
        "skillBundleUrl": f"{origin}/mcp/skill/bundle",
        "skill_markdown_url": f"{origin}/mcp/skill",
        "skillMarkdownUrl": f"{origin}/mcp/skill",
    }


_LOCAL_ACCESS_ERROR_DETAIL = "仅允许本机访问该接口"
_LOG_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]+")
_LOG_METHOD_MAX_LEN = 16
_LOG_URL_MAX_LEN = 512
_LOG_SOURCE_MAX_LEN = 512
_LOG_DETAIL_MAX_LEN = 1000


def _sanitize_log_field(value: object, *, max_len: int, default: str = "") -> str:
    if value is None:
        return default
    text = _LOG_CONTROL_CHARS_RE.sub(" ", str(value)).strip()
    if not text:
        return default
    if len(text) <= max_len:
        return text
    if max_len <= 3:
        return text[:max_len]
    return f"{text[: max_len - 3].rstrip()}..."


def _get_current_log_file_path() -> Path:
    log_file = Path(get_log_file_path())
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    if not log_file.exists():
        try:
            log_file.touch(exist_ok=True)
        except Exception:
            pass
    return log_file


def _open_path_with_default_app(path: Path) -> None:
    target = str(path)
    if os.name == "nt":
        opener = getattr(os, "startfile", None)
        if opener is None:
            raise RuntimeError("当前系统不支持默认打开文件")
        opener(target)
        return

    subprocess.Popen(["xdg-open", target])


def _is_port_available(port: int, host: str) -> bool:
    try:
        addr = (host, int(port))
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            s.bind(addr)
        return True
    except Exception:
        return False


async def _wait_for_backend_ready(health_url: str, timeout_s: float = 30.0) -> bool:
    started = time.time()
    async with httpx.AsyncClient(timeout=1.0) as client:
        while time.time() - started < timeout_s:
            try:
                resp = await client.get(health_url)
                if resp.status_code < 500:
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.3)
    return False


def _build_backend_spawn_spec(
    next_port: int,
    next_host: str | None = None,
) -> tuple[list[str], str, dict[str, str], str]:
    bind_host = str(next_host or _get_backend_bind_host())
    env = os.environ.copy()
    env["WECHAT_TOOL_PORT"] = str(int(next_port))
    env["WECHAT_TOOL_HOST"] = bind_host

    # Keep the same working directory so output paths remain consistent.
    # (When `WECHAT_TOOL_DATA_DIR` is not set, the app uses `Path.cwd()`.)
    cwd = os.getcwd()
    cwd_path = Path(cwd)

    # Ensure local imports work when running from source (repo root + src layout).
    src_dir = cwd_path / "src"
    existing_pp = str(env.get("PYTHONPATH", "") or "").strip()
    if src_dir.is_dir():
        env["PYTHONPATH"] = str(src_dir) if not existing_pp else f"{src_dir}{os.pathsep}{existing_pp}"

    if getattr(sys, "frozen", False):
        target = [sys.executable]
    else:
        main_py = cwd_path / "main.py"
        if main_py.is_file():
            target = [sys.executable, str(main_py)]
        else:
            target = [sys.executable, "-m", "wechat_decrypt_tool.backend_entry"]

    return target, cwd, env, bind_host


def _terminate_process_safely(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
    except Exception as exc:
        logger.warning("Failed to terminate spawned backend process: %s", exc)


def _spawn_backend_process(next_port: int, next_host: str | None = None) -> subprocess.Popen:
    target, cwd, env, _ = _build_backend_spawn_spec(next_port, next_host)
    return subprocess.Popen(target, cwd=cwd, env=env)


def _spawn_backend_process_after_delay(
    next_port: int,
    next_host: str,
    delay_s: float = 0.8,
    *,
    old_pid: int | None = None,
    timeout_s: float = DEFAULT_RESTART_WAIT_TIMEOUT_S,
) -> subprocess.Popen:
    target, cwd, env, bind_host = _build_backend_spawn_spec(next_port, next_host)
    wait_pid = os.getpid() if old_pid is None else int(old_pid)
    wait_args = [
        str(wait_pid),
        bind_host,
        str(int(next_port)),
        str(max(0.0, float(delay_s))),
        str(max(0.0, float(timeout_s))),
        "--",
        *target,
    ]
    # Keep the launcher independent from this process; it waits until this process
    # has exited and the target socket can be bound before spawning the replacement.
    if getattr(sys, "frozen", False):
        launcher_cmd = [sys.executable, "--wait-and-spawn", *wait_args]
    else:
        launcher_cmd = [sys.executable, "-m", "wechat_decrypt_tool.server_lifecycle", *wait_args]
    return subprocess.Popen(launcher_cmd, cwd=cwd, env=env)


async def _exit_process_after(delay_s: float) -> None:
    delay = max(0.0, float(delay_s))
    if delay > 0:
        await asyncio.sleep(delay)
    request_graceful_shutdown()


@router.get("/api/admin/log-file", summary="获取当前后端日志文件路径")
async def get_backend_log_file(request: Request) -> dict:
    require_local_request(request, allow_remote_opt_in=True, detail=_LOCAL_ACCESS_ERROR_DETAIL)
    log_file = _get_current_log_file_path()
    return {"path": str(log_file), "exists": log_file.exists()}


@router.post("/api/admin/log-file/open", summary="打开当前后端日志文件（默认仅本机；远程模式可用）")
async def open_backend_log_file(request: Request) -> dict:
    require_local_request(request, allow_remote_opt_in=True, detail=_LOCAL_ACCESS_ERROR_DETAIL)

    log_file = _get_current_log_file_path()
    try:
        _open_path_with_default_app(log_file)
    except Exception as e:
        logger.error("open_backend_log_file failed path=%s err=%s", log_file, e)
        raise HTTPException(status_code=500, detail=f"打开日志文件失败：{e}")
    return {"success": True, "path": str(log_file)}


@router.post("/api/admin/log-frontend-server-error", summary="记录前端感知到的服务器错误")
async def log_frontend_server_error(payload: dict, request: Request) -> dict:
    require_local_request(request, allow_remote_opt_in=True, detail=_LOCAL_ACCESS_ERROR_DETAIL)

    data = payload if isinstance(payload, dict) else {}
    raw_status = data.get("status")
    if isinstance(raw_status, bool):
        status = 0
    else:
        try:
            status = int(raw_status)
        except Exception:
            status = 0
    if status < 0 or status > 999:
        status = 0

    method = _sanitize_log_field(
        data.get("method"),
        max_len=_LOG_METHOD_MAX_LEN,
        default="GET",
    ).upper()
    request_url = _sanitize_log_field(data.get("request_url"), max_len=_LOG_URL_MAX_LEN)
    message = _sanitize_log_field(data.get("message"), max_len=_LOG_DETAIL_MAX_LEN)
    backend_detail = _sanitize_log_field(data.get("backend_detail"), max_len=_LOG_DETAIL_MAX_LEN)
    source = _sanitize_log_field(data.get("source"), max_len=_LOG_SOURCE_MAX_LEN)
    page_url = _sanitize_log_field(data.get("page_url"), max_len=_LOG_URL_MAX_LEN)

    logger.error(
        "[frontend-server-error] status=%s method=%s request_url=%s message=%s backend_detail=%s source=%s page_url=%s",
        status,
        method,
        request_url,
        message,
        backend_detail,
        source,
        page_url,
    )
    return {"success": True, "path": str(_get_current_log_file_path())}


@router.get("/api/admin/port", summary="获取后端端口（用于前端设置页）")
async def get_backend_port(request: Request) -> dict:
    require_local_request(request, allow_remote_opt_in=True, detail=_LOCAL_ACCESS_ERROR_DETAIL)
    port, source = read_effective_backend_port(default=DEFAULT_BACKEND_PORT)
    return {"port": port, "source": source, "default_port": DEFAULT_BACKEND_PORT}


@router.get("/api/admin/mcp-access", summary="获取 MCP 局域网接入状态")
async def get_mcp_access(request: Request) -> dict:
    require_local_request(request, allow_remote_opt_in=True, detail=_LOCAL_ACCESS_ERROR_DETAIL)
    host, source = read_effective_backend_host(default=default_backend_host())
    port, port_source = read_effective_backend_port(default=DEFAULT_BACKEND_PORT)
    return {
        "enabled": host == LAN_BACKEND_HOST,
        "host": host,
        "source": source,
        "port": port,
        "port_source": port_source,
        "default_host": LOOPBACK_BACKEND_HOST,
        "lan_host": LAN_BACKEND_HOST,
        "restart_required": False,
        **_get_mcp_access_urls(port, host),
    }


@router.get("/api/admin/mcp-token", summary="获取 MCP token（仅本机）")
async def get_mcp_token(request: Request) -> dict:
    require_strict_local_request(request, detail=_LOCAL_ACCESS_ERROR_DETAIL)

    from ..runtime_settings import ensure_mcp_token

    token, source = ensure_mcp_token()
    env_file = write_mcp_token_env_file(token)
    return {
        "success": True,
        "token": token,
        "source": source,
        "env_file": str(env_file) if env_file else None,
    }


@router.post("/api/admin/mcp-token/reset", summary="重置 MCP token（仅本机）")
async def reset_mcp_token_endpoint(request: Request) -> dict:
    require_strict_local_request(request, detail=_LOCAL_ACCESS_ERROR_DETAIL)

    previous, previous_source = read_effective_mcp_token()
    token = reset_mcp_token()
    os.environ["WECHAT_TOOL_MCP_TOKEN"] = token
    env_file = write_mcp_token_env_file(token)
    return {
        "success": True,
        "changed": token != previous,
        "token": token,
        "previous_source": previous_source,
        "source": "reset",
        "env_file": str(env_file) if env_file else None,
    }


@router.post("/api/admin/port", summary="修改后端端口并重启（默认仅本机；远程模式可用）")
async def set_backend_port(payload: dict, request: Request, background_tasks: BackgroundTasks) -> dict:
    require_local_request(request, allow_remote_opt_in=True, detail=_LOCAL_ACCESS_ERROR_DETAIL)

    global _PORT_CHANGE_IN_PROGRESS
    if _PORT_CHANGE_IN_PROGRESS:
        raise HTTPException(status_code=409, detail="端口切换中，请稍后重试")

    raw = payload.get("port") if isinstance(payload, dict) else None
    try:
        next_port = int(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="端口无效：请输入 1-65535 的整数")
    if next_port < 1 or next_port > 65535:
        raise HTTPException(status_code=400, detail="端口无效：请输入 1-65535 的整数")

    current_port, _ = read_effective_backend_port(default=DEFAULT_BACKEND_PORT)
    if next_port == int(current_port):
        write_backend_port_setting(next_port)
        env_file = write_backend_port_env_file(next_port)
        host = _format_host_for_url(_get_backend_access_host())
        return {
            "success": True,
            "changed": False,
            "port": next_port,
            "ui_url": f"http://{host}:{next_port}/",
            "env_file": str(env_file) if env_file else None,
        }

    bind_host = _get_backend_bind_host()
    if not _is_port_available(next_port, bind_host):
        raise HTTPException(status_code=409, detail=f"端口 {next_port} 已被占用，请换一个端口")

    proc = None
    _PORT_CHANGE_IN_PROGRESS = True
    try:
        try:
            proc = _spawn_backend_process(next_port)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"启动新后端进程失败：{e}")

        try:
            access_host = _get_backend_access_host()
            health_url = f"http://{_format_host_for_url(access_host)}:{next_port}/api/health"
            ok = await _wait_for_backend_ready(health_url, timeout_s=30.0)
            if not ok:
                raise HTTPException(status_code=500, detail=f"新端口启动超时：{health_url}")

            # Persist only after the new backend is confirmed ready.
            write_backend_port_setting(next_port)
            env_file = write_backend_port_env_file(next_port)
        except BaseException:
            _terminate_process_safely(proc)
            raise

        background_tasks.add_task(_exit_process_after, 0.2)

        host = _format_host_for_url(access_host)
        return {
            "success": True,
            "changed": True,
            "port": next_port,
            "ui_url": f"http://{host}:{next_port}/",
            "env_file": str(env_file) if env_file else None,
        }
    finally:
        _PORT_CHANGE_IN_PROGRESS = False


@router.post("/api/admin/mcp-access", summary="开启或关闭 MCP 局域网接入并重启后端（默认仅本机；远程模式可用）")
async def set_mcp_access(payload: dict, request: Request, background_tasks: BackgroundTasks) -> dict:
    require_local_request(request, allow_remote_opt_in=True, detail=_LOCAL_ACCESS_ERROR_DETAIL)

    global _PORT_CHANGE_IN_PROGRESS
    if _PORT_CHANGE_IN_PROGRESS:
        raise HTTPException(status_code=409, detail="后端切换中，请稍后重试")

    enabled = bool(payload.get("enabled")) if isinstance(payload, dict) else False
    next_host = LAN_BACKEND_HOST if enabled else LOOPBACK_BACKEND_HOST
    current_host = _get_backend_bind_host()
    current_port, _ = read_effective_backend_port(default=DEFAULT_BACKEND_PORT)

    if next_host == current_host:
        write_backend_host_setting(next_host)
        env_file = write_backend_host_env_file(next_host)
        return {
            "success": True,
            "changed": False,
            "enabled": enabled,
            "host": next_host,
            "port": int(current_port),
            "ui_url": f"http://{_format_host_for_url(_get_backend_access_host())}:{int(current_port)}/",
            "env_file": str(env_file) if env_file else None,
            **_get_mcp_access_urls(int(current_port), next_host),
        }

    _PORT_CHANGE_IN_PROGRESS = True
    try:
        write_backend_host_setting(next_host)
        env_file = write_backend_host_env_file(next_host)

        # Host changes keep the same port. The old socket must close before the
        # new process can bind, so start a detached launcher and then exit.
        background_tasks.add_task(_spawn_backend_process_after_delay, int(current_port), next_host, 0.8)
        background_tasks.add_task(_exit_process_after, 0.2)

        return {
            "success": True,
            "changed": True,
            "enabled": enabled,
            "host": next_host,
            "port": int(current_port),
            "ui_url": f"http://{_format_host_for_url(LOOPBACK_BACKEND_HOST)}:{int(current_port)}/",
            "env_file": str(env_file) if env_file else None,
            "restart_scheduled": True,
            **_get_mcp_access_urls(int(current_port), next_host),
        }
    finally:
        _PORT_CHANGE_IN_PROGRESS = False
