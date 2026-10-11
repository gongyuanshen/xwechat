"""xwechat FastAPI Web服务器"""

import asyncio
import mimetypes
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import FileResponse, JSONResponse
from starlette.staticfiles import StaticFiles

from .wechat_ui_bridge import WeChatBridgeError

from .logging_config import setup_logging, get_logger, install_sensitive_query_log_filter

# 初始化日志系统
setup_logging()
logger = get_logger(__name__)
request_logger = get_logger("wechat_decrypt_tool.request")

from . import __version__ as APP_VERSION
from .path_fix import PathFixRoute
from .routers.chat import router as _chat_router
from .routers.chat_contacts import router as _chat_contacts_router
from .routers.chat_export import router as _chat_export_router
from .routers.chat_media import router as _chat_media_router
from .routers.decrypt import router as _decrypt_router
from .routers.snapshot_refresh import router as _snapshot_refresh_router
from .snapshot_refresh import SNAPSHOT_REFRESH
from .snapshot_registry import SnapshotReadMiddleware, SnapshotRegistryError
from .routers.import_decrypted import router as _import_decrypted_router
from .routers.health import router as _health_router
from .routers.admin import router as _admin_router
from .routers.account_archive_export import router as _account_archive_export_router
from .routers.keys import router as _keys_router
from .routers.media import router as _media_router
from .routers.mcp import router as _mcp_router
from .routers.sns import router as _sns_router
from .routers.sns_export import router as _sns_export_router
from .routers.wechat_detection import router as _wechat_detection_router
from .routers.wrapped import router as _wrapped_router
from .routers.general import router as _general_router
from .routers.favorites import router as _favorites_router
from .routers.record_export import router as _record_export_router
from .request_logging import log_server_errors_middleware
from .perf_trace import ChatRequestPerfMiddleware
from .routers.biz import router as _biz_router
from .routers.system import router as _system_router

app = FastAPI(
    title="xwechat",
    description="xwechat 本地微信数据浏览、导出与交互工具",
    version=APP_VERSION,
)

app.add_middleware(SnapshotReadMiddleware)


@app.exception_handler(SnapshotRegistryError)
async def _snapshot_registry_error(request: Request, exc: SnapshotRegistryError):
    logger.error("Snapshot registry error: %s", exc, exc_info=True)
    return JSONResponse(status_code=409, content={"detail": str(exc), "type": type(exc).__name__})


# 设置自定义路由类
app.router.route_class = PathFixRoute

# Enable CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    expose_headers=['X-WCDA-AI-Trace', 'X-WCDA-AI-Diagnostic', 'Content-Disposition'],
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _log_server_errors(request: Request, call_next):
    # Uvicorn applies its logging config after this module is imported. Reinstall
    # the idempotent filter before access logging runs for each request.
    install_sensitive_query_log_filter()
    return await log_server_errors_middleware(request_logger, request, call_next)


app.add_middleware(ChatRequestPerfMiddleware, logger=request_logger)


from .routers.ai import router as _ai_router
app.include_router(_ai_router)
from .routers.auto_reply import router as _auto_reply_router
app.include_router(_auto_reply_router)
from .routers.ai_insights import router as _ai_insights_router
app.include_router(_ai_insights_router)
from .routers.ai_agent import router as _ai_agent_router
app.include_router(_ai_agent_router)
from .routers.library import router as _library_router
app.include_router(_library_router)
from .routers.attachments import router as _attachments_router
app.include_router(_attachments_router)
from .routers.local_search import router as _local_search_router
app.include_router(_local_search_router)
from .routers.local_acceleration import router as _local_acceleration_router
app.include_router(_local_acceleration_router)
from .routers.storage import router as _storage_router
app.include_router(_storage_router)
app.include_router(_health_router)
app.include_router(_admin_router)
app.include_router(_account_archive_export_router)
app.include_router(_wechat_detection_router)
app.include_router(_import_decrypted_router)
app.include_router(_decrypt_router)
app.include_router(_snapshot_refresh_router)
app.include_router(_keys_router)
app.include_router(_media_router)
app.include_router(_mcp_router)
app.include_router(_chat_router)
app.include_router(_chat_contacts_router)
app.include_router(_chat_export_router)
app.include_router(_chat_media_router)
app.include_router(_sns_router)
app.include_router(_sns_export_router)
app.include_router(_wrapped_router)
app.include_router(_biz_router)
app.include_router(_general_router)
app.include_router(_favorites_router)
app.include_router(_record_export_router)
app.include_router(_system_router)
from .routers import chat_send as _chat_send_router
app.include_router(_chat_send_router.router, prefix="/api", tags=["chat_send"])


@app.exception_handler(WeChatBridgeError)
async def wechat_bridge_error_handler(request: Request, exc: WeChatBridgeError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "detail": exc.detail},
    )


# Python's MIME database inherits Windows registry overrides.  Keep the
# generated frontend's static asset types deterministic across installations.
for _media_type, _suffix in (
    ("text/javascript", ".js"),
    ("text/javascript", ".mjs"),
    ("text/css", ".css"),
    ("application/json", ".json"),
    ("image/svg+xml", ".svg"),
):
    mimetypes.add_type(_media_type, _suffix)


class _SPAStaticFiles(StaticFiles):
    """StaticFiles with a SPA fallback (Nuxt generate output)."""

    _CONTENT_TYPE_OVERRIDES = {
        ".js": "text/javascript; charset=utf-8",
        ".mjs": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._fallback_200 = Path(str(self.directory)) / "200.html"
        self._fallback_index = Path(str(self.directory)) / "index.html"

    @staticmethod
    def _normalize_path(path: str) -> str:
        # Starlette supplies filesystem paths using the host OS separator.
        return str(path or "").replace(os.sep, "/").strip().lstrip("/")

    @classmethod
    def _is_shell_path(cls, path: str) -> bool:
        normalized = cls._normalize_path(path)
        return normalized in {"", "index.html", "200.html", "_payload.json"} or normalized.startswith(
            "_payload.json/"
        )

    @classmethod
    def _apply_cache_headers(cls, path: str, response):
        normalized = cls._normalize_path(path)
        try:
            if cls._is_shell_path(normalized):
                response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
            elif normalized.startswith("_nuxt/"):
                response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        except Exception:
            pass
        return response

    async def get_response(self, path: str, scope):  # type: ignore[override]
        normalized = self._normalize_path(path)
        try:
            response = await super().get_response(path, scope)
            content_type = self._CONTENT_TYPE_OVERRIDES.get(Path(normalized).suffix.lower())
            if content_type:
                response.headers["content-type"] = content_type
            return self._apply_cache_headers(normalized, response)
        except StarletteHTTPException as exc:
            # Missing API endpoints must remain errors, not a successful HTML page.
            if exc.status_code != 404 or normalized == "api" or normalized.startswith("api/"):
                raise

            # For client-side routes (no file extension), return Nuxt's SPA fallback.
            name = Path(path).name
            if "." in name:
                raise

            if self._fallback_200.exists():
                return self._apply_cache_headers("200.html", FileResponse(str(self._fallback_200)))
            return self._apply_cache_headers("index.html", FileResponse(str(self._fallback_index)))


def _maybe_mount_frontend() -> None:
    """Serve the generated Nuxt static site at `/` if present.

    This keeps web + desktop UI identical when the desktop shell (Electron) loads
    http://127.0.0.1:<port>/ from the same backend that serves `/api/*`.
    """

    ui_dir_env = os.environ.get("WECHAT_TOOL_UI_DIR", "").strip()

    candidates: list[Path] = []
    if ui_dir_env:
        candidates.append(Path(ui_dir_env))

    # Repo default: `frontend/.output/public` after `npm --prefix frontend run generate`.
    repo_root = Path(__file__).resolve().parents[2]
    candidates.append(repo_root / "frontend" / ".output" / "public")

    ui_dir: Path | None = None
    for p in candidates:
        try:
            if (p / "index.html").is_file():
                ui_dir = p
                break
        except Exception:
            continue

    if not ui_dir:
        return

    try:
        app.mount("/", _SPAStaticFiles(directory=str(ui_dir), html=True), name="ui")
        logger.info("Serving frontend UI from: %s", ui_dir)
    except Exception:
        logger.exception("Failed to mount frontend UI from: %s", ui_dir)


_maybe_mount_frontend()


@app.on_event("startup")
async def _startup_background_jobs() -> None:
    from .ai.lifecycle import start_services
    await start_services()


@app.on_event("shutdown")
async def _shutdown_background_jobs() -> None:
    from .ai.lifecycle import stop_services
    await asyncio.to_thread(SNAPSHOT_REFRESH.shutdown)
    await stop_services()


if __name__ == "__main__":
    import uvicorn

    from .runtime_settings import default_backend_host, read_effective_backend_port

    host = os.environ.get("WECHAT_TOOL_HOST", default_backend_host())
    port, _ = read_effective_backend_port(default=10392)
    uvicorn.run(app, host=host, port=port, log_config=None)
