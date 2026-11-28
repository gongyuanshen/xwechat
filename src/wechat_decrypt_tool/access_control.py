"""Centralized local-only access control and anti-cross-origin boundaries."""

from __future__ import annotations

import ipaddress
import logging
import posixpath
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import unquote, urlsplit

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from .runtime_settings import RuntimeSettingsError, remote_calls_enabled

logger = logging.getLogger(__name__)

LOCAL_CORS_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"

DEFAULT_LOCAL_ONLY_DETAIL = "仅允许本机应用访问"
_INVALID_HOST_DETAIL = "无效的 Host 请求头"
_NON_LOOPBACK_HOST_DETAIL = "仅允许通过本机地址访问"
_INVALID_ORIGIN_DETAIL = "拒绝非本机来源的跨站请求"
_INVALID_MCP_ORIGIN_DETAIL = "拒绝非本机浏览器来源访问 MCP 接口"

_HOST_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_STRICT_LOCAL_PATHS = ("/api/admin/mcp-token",)


def _has_control_or_space(value: str) -> bool:
    return any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value)


def _parse_port(raw_port: str) -> int | None:
    if not raw_port or not raw_port.isascii() or not raw_port.isdigit():
        return None
    port = int(raw_port, 10)
    if port < 1 or port > 65535:
        return None
    return port


def _default_port_for_scheme(scheme: str) -> int:
    return 443 if str(scheme or "").lower() in {"https", "wss"} else 80


def is_loopback_client_ip(host: str | None) -> bool:
    """Return True only for literal loopback client IP addresses."""

    if not isinstance(host, str):
        return False
    value = host.strip()
    if not value or "%" in value:
        return False

    bracketed = value.startswith("[") and value.endswith("]")
    if bracketed:
        value = value[1:-1]
        if not value or "[" in value or "]" in value:
            return False

    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False

    if bracketed and not isinstance(address, ipaddress.IPv6Address):
        return False

    return bool(
        address.is_loopback
        or (
            isinstance(address, ipaddress.IPv6Address)
            and address.ipv4_mapped is not None
            and address.ipv4_mapped.is_loopback
        )
    )


def parse_host_header(raw_host: str | None) -> tuple[str, int | None] | None:
    """Strictly parse an HTTP Host header into (normalized_hostname, port)."""

    if not isinstance(raw_host, str):
        return None
    value = raw_host.strip()
    if not value or _has_control_or_space(value):
        return None
    if any(ch in value for ch in ("/", "\\", "@", "?", "#", ",", "%")):
        return None

    if value.startswith("["):
        end_idx = value.find("]")
        if end_idx <= 1:
            return None
        ipv6_text = value[1:end_idx]
        rest = value[end_idx + 1 :]
        try:
            ipv6_addr = ipaddress.IPv6Address(ipv6_text)
        except ValueError:
            return None

        port: int | None = None
        if rest:
            if not rest.startswith(":"):
                return None
            port = _parse_port(rest[1:])
            if port is None:
                return None
        return str(ipv6_addr), port

    if "]" in value:
        return None

    colon_count = value.count(":")
    if colon_count > 1:
        return None
    if colon_count == 1:
        host_part, port_text = value.split(":", 1)
        port = _parse_port(port_text)
        if port is None:
            return None
    else:
        host_part = value
        port = None

    if not host_part or len(host_part) > 253 or not host_part.isascii():
        return None

    labels = host_part.split(".")
    if any(not _HOST_LABEL_RE.match(label) for label in labels):
        return None

    # Reject shorthand/invalid numeric hosts like 127.1 or 0177.0.0.1.
    if all(label.isdigit() for label in labels):
        try:
            ipv4_addr = ipaddress.IPv4Address(host_part)
        except ValueError:
            return None
        return str(ipv4_addr), port

    return host_part.lower(), port


def is_loopback_hostname(hostname: str | None) -> bool:
    """Return True for bare loopback hostnames or loopback IP literals."""

    if not isinstance(hostname, str):
        return False
    value = hostname.strip()
    if not value:
        return False
    if value.lower() == "localhost":
        return True
    return is_loopback_client_ip(value)


def is_loopback_host(host: str | None) -> bool:
    """Return True for loopback hostnames, loopback IPs, or loopback Host headers."""

    if not isinstance(host, str):
        return False
    value = host.strip()
    if not value:
        return False
    if is_loopback_client_ip(value):
        return True
    parsed = parse_host_header(value)
    if parsed is None:
        return False
    return is_loopback_hostname(parsed[0])


def parse_origin_header(raw_origin: str | None) -> tuple[str, str, int | None] | None:
    """Strictly parse a serialized HTTP(S) Origin header into (scheme, hostname, port)."""

    if not isinstance(raw_origin, str):
        return None
    origin = raw_origin.strip()
    if not origin or _has_control_or_space(origin):
        return None
    if "?" in origin or "#" in origin:
        return None

    try:
        parsed = urlsplit(origin)
    except ValueError:
        return None

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        return None
    if not origin.lower().startswith(f"{scheme}://"):
        return None
    if parsed.path or parsed.query or parsed.fragment:
        return None
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        return None

    host_parsed = parse_host_header(parsed.netloc)
    if host_parsed is None:
        return None
    hostname, port = host_parsed
    return scheme, hostname, port


def is_loopback_origin(origin: str | None) -> bool:
    """Return True only for http(s) origins pointing to a loopback host."""

    parsed = parse_origin_header(origin)
    if parsed is None:
        return False
    _, hostname, _ = parsed
    return is_loopback_hostname(hostname)


def is_same_host_origin(
    origin: str | None,
    host_header: str | None,
    *,
    default_scheme: str = "http",
) -> bool:
    """Return True when Origin matches the request's Host (hostname + effective port)."""

    parsed_origin = parse_origin_header(origin)
    parsed_host = parse_host_header(host_header)
    if parsed_origin is None or parsed_host is None:
        return False

    origin_scheme, origin_host, origin_port = parsed_origin
    host_name, host_port = parsed_host
    if origin_host != host_name:
        return False

    effective_origin_port = origin_port if origin_port is not None else _default_port_for_scheme(origin_scheme)
    effective_host_port = host_port if host_port is not None else _default_port_for_scheme(default_scheme)
    return effective_origin_port == effective_host_port


def _has_unsafe_path_syntax(path: str, raw_path: bytes | None = None) -> bool:
    if not path or not path.startswith("/"):
        return True
    if "\\" in path or "\x00" in path or "//" in path:
        return True
    if any(segment in {".", ".."} for segment in path.split("/")):
        return True

    if raw_path:
        raw_text = raw_path.split(b"?", 1)[0].decode("latin-1", errors="strict")
        lowered = raw_text.lower()
        if (
            not raw_text.startswith("/")
            or "\\" in raw_text
            or "\x00" in raw_text
            or "//" in raw_text
            or "%2e" in lowered
            or "%2f" in lowered
            or "%5c" in lowered
            or "%00" in lowered
        ):
            return True
        if any(segment in {".", ".."} for segment in raw_text.split("/")):
            return True

    return False


def _normalize_path_for_strict_match(raw: str) -> str:
    text = str(raw or "").replace("\\", "/")
    if not text.startswith("/"):
        text = "/" + text
    normalized = posixpath.normpath(text)
    while normalized.startswith("//"):
        normalized = normalized[1:]
    normalized = normalized.rstrip("/") or "/"
    return normalized.lower()


def is_mcp_path(path: str, raw_path: bytes | None = None) -> bool:
    """Return True only for canonical, case-sensitive /mcp and /mcp/* paths."""

    if _has_unsafe_path_syntax(path, raw_path):
        return False
    return path == "/mcp" or path.startswith("/mcp/")


def is_strict_local_path(path: str, raw_path: bytes | None = None) -> bool:
    """Return True for paths that must stay strictly loopback-only in all modes."""

    candidates = [path]
    if raw_path:
        raw_text = raw_path.split(b"?", 1)[0].decode("latin-1", errors="ignore")
        candidates.append(raw_text)
        candidates.append(unquote(raw_text))

    for candidate in candidates:
        normalized = _normalize_path_for_strict_match(candidate)
        for strict_path in _STRICT_LOCAL_PATHS:
            if normalized == strict_path or normalized.startswith(f"{strict_path}/"):
                return True
    return False


def _evaluate_access(
    *,
    client_host: str,
    host_headers: Sequence[str],
    origin_headers: Sequence[str],
    scheme: str,
    require_loopback_client_and_host: bool,
    allow_same_host_origin: bool,
    origin_detail: str = _INVALID_ORIGIN_DETAIL,
) -> str | None:
    if len(host_headers) != 1:
        return _INVALID_HOST_DETAIL
    host_header = host_headers[0]
    parsed_host = parse_host_header(host_header)
    if parsed_host is None:
        return _INVALID_HOST_DETAIL

    if require_loopback_client_and_host:
        if not is_loopback_client_ip(client_host):
            return DEFAULT_LOCAL_ONLY_DETAIL
        if not is_loopback_hostname(parsed_host[0]):
            return _NON_LOOPBACK_HOST_DETAIL

    if len(origin_headers) > 1:
        return origin_detail
    if len(origin_headers) == 1:
        origin_header = origin_headers[0]
        if is_loopback_origin(origin_header):
            return None
        if allow_same_host_origin and is_same_host_origin(
            origin_header,
            host_header,
            default_scheme=scheme,
        ):
            return None
        return origin_detail

    return None


def _extract_request_parts(request: Request) -> tuple[str, list[str], list[str], str]:
    client_host = str(getattr(request.client, "host", "") or "")
    host_headers = request.headers.getlist("host")
    origin_headers = request.headers.getlist("origin")
    scheme = str(request.scope.get("scheme") or "http").lower()
    return client_host, host_headers, origin_headers, scheme


def _extract_scope_header_values(scope: Mapping[str, Any], header_name: bytes) -> list[str]:
    target = header_name.lower()
    values: list[str] = []
    for raw_key, raw_value in scope.get("headers") or ():
        if bytes(raw_key).lower() == target:
            values.append(bytes(raw_value).decode("latin-1"))
    return values


def require_strict_local_request(request: Request, detail: str | None = None) -> None:
    """Enforce strict loopback client IP, Host, and Origin (if present).

    Never bypassed by WECHAT_TOOL_ALLOW_REMOTE_CALLS.
    """

    client_host, host_headers, origin_headers, scheme = _extract_request_parts(request)
    violation = _evaluate_access(
        client_host=client_host,
        host_headers=host_headers,
        origin_headers=origin_headers,
        scheme=scheme,
        require_loopback_client_and_host=True,
        allow_same_host_origin=False,
    )
    if violation is not None:
        raise HTTPException(status_code=403, detail=detail or violation)


def require_local_request(
    request: Request,
    *,
    allow_remote_opt_in: bool = True,
    detail: str | None = None,
) -> None:
    """Enforce local access by default, optionally honoring WECHAT_TOOL_ALLOW_REMOTE_CALLS.

    Even when remote opt-in is active, a valid Host header is required and
    cross-site browser Origin headers are rejected.
    """

    if not allow_remote_opt_in or not remote_calls_enabled():
        require_strict_local_request(request, detail=detail)
        return

    client_host, host_headers, origin_headers, scheme = _extract_request_parts(request)
    violation = _evaluate_access(
        client_host=client_host,
        host_headers=host_headers,
        origin_headers=origin_headers,
        scheme=scheme,
        require_loopback_client_and_host=False,
        allow_same_host_origin=True,
    )
    if violation is not None:
        raise HTTPException(status_code=403, detail=detail or violation)


def local_only(request: Request) -> None:
    """FastAPI dependency enforcing strict loopback-only access."""

    require_strict_local_request(request)


class AccessControlMiddleware:
    """ASGI middleware enforcing local-only and cross-origin boundaries."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        scope_type = scope.get("type")
        if scope_type not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        raw_path = scope.get("raw_path")
        raw_path_bytes = bytes(raw_path) if isinstance(raw_path, (bytes, bytearray)) else None
        scheme = str(scope.get("scheme") or "http").lower()
        client = scope.get("client")
        client_host = str(client[0]) if client and len(client) >= 1 and client[0] is not None else ""
        host_headers = _extract_scope_header_values(scope, b"host")
        origin_headers = _extract_scope_header_values(scope, b"origin")

        if is_strict_local_path(path, raw_path_bytes):
            violation = _evaluate_access(
                client_host=client_host,
                host_headers=host_headers,
                origin_headers=origin_headers,
                scheme=scheme,
                require_loopback_client_and_host=True,
                allow_same_host_origin=False,
            )
        elif is_mcp_path(path, raw_path_bytes):
            violation = _evaluate_access(
                client_host=client_host,
                host_headers=host_headers,
                origin_headers=origin_headers,
                scheme=scheme,
                require_loopback_client_and_host=False,
                allow_same_host_origin=False,
                origin_detail=_INVALID_MCP_ORIGIN_DETAIL,
            )
        else:
            try:
                allow_remote = remote_calls_enabled()
            except RuntimeSettingsError as exc:
                logger.error("Runtime settings error in access control: %s", exc, exc_info=True)
                if scope_type == "websocket":
                    await send({"type": "websocket.close", "code": 1011, "reason": "Invalid runtime settings"})
                    return
                response = JSONResponse(
                    status_code=500,
                    content={"detail": str(exc), "type": type(exc).__name__},
                )
                await response(scope, receive, send)
                return

            if allow_remote:
                violation = _evaluate_access(
                    client_host=client_host,
                    host_headers=host_headers,
                    origin_headers=origin_headers,
                    scheme=scheme,
                    require_loopback_client_and_host=False,
                    allow_same_host_origin=True,
                )
            else:
                violation = _evaluate_access(
                    client_host=client_host,
                    host_headers=host_headers,
                    origin_headers=origin_headers,
                    scheme=scheme,
                    require_loopback_client_and_host=True,
                    allow_same_host_origin=False,
                )

        if violation is not None:
            if scope_type == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            response = JSONResponse(status_code=403, content={"detail": violation})
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
