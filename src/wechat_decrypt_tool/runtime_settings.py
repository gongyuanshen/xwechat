from __future__ import annotations

import json
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path


RUNTIME_SETTINGS_FILENAME = "runtime_settings.json"
BACKEND_PORT_KEY = "backend_port"
BACKEND_HOST_KEY = "backend_host"
MCP_TOKEN_KEY = "mcp_token"
VOICE_TRANSCRIPTION_DEVICE_KEY = "voice_transcription_device"
VOICE_TRANSCRIPTION_MODEL_KEY = "voice_transcription_model"
ENV_PORT_KEY = "WECHAT_TOOL_PORT"
ENV_HOST_KEY = "WECHAT_TOOL_HOST"
ENV_ALLOW_REMOTE_CALLS_KEY = "WECHAT_TOOL_ALLOW_REMOTE_CALLS"
ENV_MCP_TOKEN_KEY = "WECHAT_TOOL_MCP_TOKEN"
ENV_VOICE_TRANSCRIPTION_DEVICE_KEY = "WECHAT_TOOL_WHISPER_DEVICE"
ENV_VOICE_TRANSCRIPTION_MODEL_KEY = "WECHAT_TOOL_WHISPER_MODEL"
ENV_FILE_KEY = "WECHAT_TOOL_ENV_FILE"
DEFAULT_ENV_FILENAME = ".env"
LOOPBACK_BACKEND_HOST = "127.0.0.1"
LAN_BACKEND_HOST = "0.0.0.0"
VOICE_TRANSCRIPTION_DEVICE_CPU = "cpu"
VOICE_TRANSCRIPTION_DEVICE_CUDA = "cuda"

_TRUE_BOOL_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_BOOL_VALUES = frozenset({"0", "false", "no", "off"})


class RuntimeSettingsError(RuntimeError):
    """Raised when runtime settings or related env files are invalid, corrupt, or fail I/O."""


def remote_calls_enabled(default: bool = False) -> bool:
    """Return whether caller-location checks are explicitly relaxed.

    This is deliberately opt-in. It does not disable MCP token checks,
    path validation, or resource limits.
    """

    raw = os.environ.get(ENV_ALLOW_REMOTE_CALLS_KEY)
    if raw is None:
        return bool(default)
    normalized = str(raw).strip().lower()
    if not normalized:
        return bool(default)
    if normalized in _TRUE_BOOL_VALUES:
        return True
    if normalized in _FALSE_BOOL_VALUES:
        return False
    raise RuntimeSettingsError(
        f"Invalid {ENV_ALLOW_REMOTE_CALLS_KEY} environment variable value: {raw!r}. "
        "Expected one of: 1, true, yes, on, 0, false, no, off."
    )


def default_backend_host() -> str:
    """Choose the bind default without overriding explicit host settings."""

    return LAN_BACKEND_HOST if remote_calls_enabled() else LOOPBACK_BACKEND_HOST


def _parse_port(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        port = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw or not raw.isascii() or not raw.isdigit():
            return None
        port = int(raw, 10)
    else:
        return None
    if port < 1 or port > 65535:
        return None
    return port


def _normalize_host(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip().lower()
    if raw in {LOOPBACK_BACKEND_HOST, "localhost", "::1"}:
        return LOOPBACK_BACKEND_HOST
    if raw in {LAN_BACKEND_HOST, "::"}:
        return LAN_BACKEND_HOST
    return None


def _normalize_mcp_token(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if len(raw) < 16 or len(raw) > 512:
        return None
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        return None
    return raw


def _normalize_voice_transcription_device(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip().lower()
    if raw in {VOICE_TRANSCRIPTION_DEVICE_CPU, VOICE_TRANSCRIPTION_DEVICE_CUDA}:
        return raw
    return None


def _normalize_voice_transcription_model(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw or len(raw) > 512 or any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        return None
    return raw


def generate_mcp_token() -> str:
    return secrets.token_urlsafe(32)


def _write_text_atomic(path: Path, content: str, *, description: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeSettingsError(
            f"Failed to create parent directory for {description} '{path}': {exc}"
        ) from exc

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        tmp_path = None
    except OSError as exc:
        raise RuntimeSettingsError(f"Failed to write {description} '{path}': {exc}") from exc
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _read_runtime_settings() -> dict:
    path = get_runtime_settings_path()
    try:
        exists = path.exists()
        is_symlink = path.is_symlink()
    except OSError as exc:
        raise RuntimeSettingsError(f"Failed to inspect runtime settings file '{path}': {exc}") from exc

    if not exists and not is_symlink:
        return {}

    try:
        if not path.is_file():
            raise RuntimeSettingsError(
                f"Runtime settings path '{path}' exists but is not a regular file."
            )
        text = path.read_text(encoding="utf-8")
    except RuntimeSettingsError:
        raise
    except UnicodeDecodeError as exc:
        raise RuntimeSettingsError(
            f"Runtime settings file '{path}' is not valid UTF-8: {exc}"
        ) from exc
    except OSError as exc:
        raise RuntimeSettingsError(f"Failed to read runtime settings file '{path}': {exc}") from exc

    if text.startswith("\ufeff"):
        text = text[1:]
    if not text.strip():
        raise RuntimeSettingsError(
            f"Runtime settings file '{path}' is empty; expected a JSON object."
        )

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeSettingsError(
            f"Runtime settings file '{path}' contains invalid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeSettingsError(
            f"Runtime settings file '{path}' must contain a JSON object, got {type(data).__name__}."
        )
    return data


def _write_runtime_settings(data: dict) -> None:
    if not isinstance(data, dict):
        raise RuntimeSettingsError(
            f"Runtime settings payload must be a dict, got {type(data).__name__}."
        )

    path = get_runtime_settings_path()
    if not data:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise RuntimeSettingsError(
                f"Failed to remove empty runtime settings file '{path}': {exc}"
            ) from exc
        return

    try:
        payload = json.dumps(data, ensure_ascii=False, indent=2)
    except (TypeError, ValueError) as exc:
        raise RuntimeSettingsError(
            f"Failed to serialize runtime settings for '{path}': {exc}"
        ) from exc

    _write_text_atomic(path, payload, description="runtime settings file")


def get_runtime_settings_path() -> Path:
    from .app_paths import get_output_dir

    return get_output_dir() / RUNTIME_SETTINGS_FILENAME


def _validate_port(value: object, *, source: str) -> int:
    port = _parse_port(value)
    if port is None:
        raise RuntimeSettingsError(
            f"Invalid {source} value: {value!r}. Expected an integer between 1 and 65535."
        )
    return port


def _validate_host(value: object, *, source: str) -> str:
    host = _normalize_host(value)
    if host is None:
        raise RuntimeSettingsError(
            f"Invalid {source} value: {value!r}. Expected one of: 127.0.0.1, localhost, ::1, 0.0.0.0, ::."
        )
    return host


def _validate_mcp_token(value: object, *, source: str) -> str:
    token = _normalize_mcp_token(value)
    if token is None:
        raise RuntimeSettingsError(
            f"Invalid {source} value: expected a 16-512 character string without whitespace or control characters."
        )
    return token


def _validate_voice_transcription_device(value: object, *, source: str) -> str:
    device = _normalize_voice_transcription_device(value)
    if device is None:
        raise RuntimeSettingsError(
            f"Invalid {source} value: {value!r}. Expected 'cpu' or 'cuda'."
        )
    return device


def _validate_voice_transcription_model(value: object, *, source: str) -> str:
    model = _normalize_voice_transcription_model(value)
    if model is None:
        raise RuntimeSettingsError(
            f"Invalid {source} value: {value!r}. Expected a non-empty string up to 512 characters without control characters."
        )
    return model


def _read_non_empty_env(env_key: str) -> str | None:
    raw = os.environ.get(env_key)
    if raw is None or not str(raw).strip():
        return None
    return raw


def read_backend_port_setting() -> int | None:
    data = _read_runtime_settings()
    raw = data.get(BACKEND_PORT_KEY)
    if raw is None:
        return None
    return _validate_port(raw, source=f"'{BACKEND_PORT_KEY}' in '{get_runtime_settings_path()}'")


def write_backend_port_setting(port: int | None) -> None:
    safe_port = None if port is None else _validate_port(port, source=f"'{BACKEND_PORT_KEY}'")
    data = _read_runtime_settings()
    if safe_port is None:
        data.pop(BACKEND_PORT_KEY, None)
    else:
        data[BACKEND_PORT_KEY] = safe_port
    _write_runtime_settings(data)


def read_effective_backend_port(default: int) -> tuple[int, str]:
    """Return (port, source) where source is one of: env | settings | default."""

    env_raw = _read_non_empty_env(ENV_PORT_KEY)
    if env_raw is not None:
        return _validate_port(env_raw, source=f"{ENV_PORT_KEY} environment variable"), "env"

    settings_port = read_backend_port_setting()
    if settings_port is not None:
        return settings_port, "settings"

    return _validate_port(default, source="default backend port"), "default"


def read_backend_host_setting() -> str | None:
    data = _read_runtime_settings()
    raw = data.get(BACKEND_HOST_KEY)
    if raw is None:
        return None
    return _validate_host(raw, source=f"'{BACKEND_HOST_KEY}' in '{get_runtime_settings_path()}'")


def write_backend_host_setting(host: str | None) -> None:
    safe_host = None if host is None else _validate_host(host, source=f"'{BACKEND_HOST_KEY}'")
    data = _read_runtime_settings()
    if safe_host is None:
        data.pop(BACKEND_HOST_KEY, None)
    else:
        data[BACKEND_HOST_KEY] = safe_host
    _write_runtime_settings(data)


def read_effective_backend_host(default: str = LOOPBACK_BACKEND_HOST) -> tuple[str, str]:
    """Return (host, source) where source is one of: env | settings | default."""

    env_raw = _read_non_empty_env(ENV_HOST_KEY)
    if env_raw is not None:
        return _validate_host(env_raw, source=f"{ENV_HOST_KEY} environment variable"), "env"

    settings_host = read_backend_host_setting()
    if settings_host is not None:
        return settings_host, "settings"

    return _validate_host(default, source="default backend host"), "default"


def read_mcp_token_setting() -> str | None:
    data = _read_runtime_settings()
    raw = data.get(MCP_TOKEN_KEY)
    if raw is None:
        return None
    return _validate_mcp_token(raw, source=f"'{MCP_TOKEN_KEY}' in '{get_runtime_settings_path()}'")


def write_mcp_token_setting(token: str | None) -> None:
    safe_token = None if token is None else _validate_mcp_token(token, source=f"'{MCP_TOKEN_KEY}'")
    data = _read_runtime_settings()
    if safe_token is None:
        data.pop(MCP_TOKEN_KEY, None)
    else:
        data[MCP_TOKEN_KEY] = safe_token
    _write_runtime_settings(data)


def read_effective_mcp_token() -> tuple[str | None, str]:
    """Return (token, source) where source is one of: env | settings | missing."""

    env_raw = _read_non_empty_env(ENV_MCP_TOKEN_KEY)
    if env_raw is not None:
        return _validate_mcp_token(env_raw, source=f"{ENV_MCP_TOKEN_KEY} environment variable"), "env"

    settings_token = read_mcp_token_setting()
    if settings_token is not None:
        return settings_token, "settings"

    return None, "missing"


def read_voice_transcription_device_setting() -> str | None:
    data = _read_runtime_settings()
    raw = data.get(VOICE_TRANSCRIPTION_DEVICE_KEY)
    if raw is None:
        return None
    return _validate_voice_transcription_device(
        raw,
        source=f"'{VOICE_TRANSCRIPTION_DEVICE_KEY}' in '{get_runtime_settings_path()}'",
    )


def write_voice_transcription_device_setting(device: str | None) -> None:
    safe_device = (
        None
        if device is None
        else _validate_voice_transcription_device(device, source=f"'{VOICE_TRANSCRIPTION_DEVICE_KEY}'")
    )
    data = _read_runtime_settings()
    if safe_device is None:
        data.pop(VOICE_TRANSCRIPTION_DEVICE_KEY, None)
    else:
        data[VOICE_TRANSCRIPTION_DEVICE_KEY] = safe_device
    _write_runtime_settings(data)


def read_effective_voice_transcription_device(default: str = VOICE_TRANSCRIPTION_DEVICE_CPU) -> tuple[str, str]:
    """Return the voice device preference and its source: env | settings | default."""

    env_raw = _read_non_empty_env(ENV_VOICE_TRANSCRIPTION_DEVICE_KEY)
    if env_raw is not None:
        return (
            _validate_voice_transcription_device(
                env_raw,
                source=f"{ENV_VOICE_TRANSCRIPTION_DEVICE_KEY} environment variable",
            ),
            "env",
        )

    settings_device = read_voice_transcription_device_setting()
    if settings_device is not None:
        return settings_device, "settings"

    return _validate_voice_transcription_device(default, source="default voice transcription device"), "default"


def read_voice_transcription_model_setting() -> str | None:
    data = _read_runtime_settings()
    raw = data.get(VOICE_TRANSCRIPTION_MODEL_KEY)
    if raw is None:
        return None
    return _validate_voice_transcription_model(
        raw,
        source=f"'{VOICE_TRANSCRIPTION_MODEL_KEY}' in '{get_runtime_settings_path()}'",
    )


def write_voice_transcription_model_setting(model: str | None) -> None:
    safe_model = (
        None
        if model is None
        else _validate_voice_transcription_model(model, source=f"'{VOICE_TRANSCRIPTION_MODEL_KEY}'")
    )
    data = _read_runtime_settings()
    if safe_model is None:
        data.pop(VOICE_TRANSCRIPTION_MODEL_KEY, None)
    else:
        data[VOICE_TRANSCRIPTION_MODEL_KEY] = safe_model
    _write_runtime_settings(data)


def read_effective_voice_transcription_model(default: str = "zipformer-small-ctc-int8") -> tuple[str, str]:
    """Return the Whisper model preference and its source: env | settings | default."""

    env_raw = _read_non_empty_env(ENV_VOICE_TRANSCRIPTION_MODEL_KEY)
    if env_raw is not None:
        return (
            _validate_voice_transcription_model(
                env_raw,
                source=f"{ENV_VOICE_TRANSCRIPTION_MODEL_KEY} environment variable",
            ),
            "env",
        )

    settings_model = read_voice_transcription_model_setting()
    if settings_model is not None:
        return settings_model, "settings"

    return _validate_voice_transcription_model(default, source="default voice transcription model"), "default"


def ensure_mcp_token() -> tuple[str, str]:
    token, source = read_effective_mcp_token()
    if token:
        return token, source

    token = generate_mcp_token()
    write_mcp_token_setting(token)
    return token, "generated"


def reset_mcp_token() -> str:
    token = generate_mcp_token()
    write_mcp_token_setting(token)
    return token


def get_env_file_path() -> Path | None:
    """Best-effort env file path for `uv run` (defaults to repo root `.env`)."""

    v = str(os.environ.get(ENV_FILE_KEY, "") or "").strip()
    if v:
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in v):
            raise RuntimeSettingsError(f"Invalid {ENV_FILE_KEY} environment variable value.")
        return Path(v).expanduser()

    # Frozen builds never load `.env`, and their cwd sits inside the install payload.
    if getattr(sys, "frozen", False):
        return None

    cwd = Path.cwd()
    # Heuristic: only write `.env` in a project root (avoid polluting random dirs).
    try:
        if (cwd / "pyproject.toml").is_file():
            return cwd / DEFAULT_ENV_FILENAME
    except OSError as exc:
        raise RuntimeSettingsError(
            f"Failed to inspect project directory '{cwd}' for {DEFAULT_ENV_FILENAME}: {exc}"
        ) from exc

    return None


def _set_env_var_in_file(env_file: Path, key: str, value: str | None) -> bool:
    try:
        exists = env_file.exists()
        is_symlink = env_file.is_symlink()
    except (OSError, ValueError) as exc:
        raise RuntimeSettingsError(f"Failed to inspect env file '{env_file}': {exc}") from exc

    if exists or is_symlink:
        try:
            if not env_file.is_file():
                raise RuntimeSettingsError(
                    f"Env file path '{env_file}' exists but is not a regular file."
                )
            raw = env_file.read_text(encoding="utf-8")
        except RuntimeSettingsError:
            raise
        except UnicodeDecodeError as exc:
            raise RuntimeSettingsError(f"Env file '{env_file}' is not valid UTF-8: {exc}") from exc
        except OSError as exc:
            raise RuntimeSettingsError(f"Failed to read env file '{env_file}': {exc}") from exc
    else:
        if value is None:
            return True
        raw = ""

    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=")
    lines = raw.splitlines(keepends=True) if raw else []
    out: list[str] = []
    replaced = False
    for line in lines:
        if pattern.match(line):
            if value is None:
                continue
            if not replaced:
                out.append(f"{key}={value}\n")
                replaced = True
            continue
        out.append(line)

    if value is not None and not replaced:
        if out and not out[-1].endswith("\n"):
            out[-1] = out[-1] + "\n"
        out.append(f"{key}={value}\n")

    _write_text_atomic(env_file, "".join(out), description="env file")
    return True


def write_backend_port_env_file(port: int | None) -> Path | None:
    """Write `WECHAT_TOOL_PORT` into a `.env` file so `uv run main.py` picks it up on restart.

    Note: `uv` doesn't override already-set env vars; `.env` only applies when the variable is not
    present in the current shell/session.
    """

    safe_port = None if port is None else _validate_port(port, source=f"'{BACKEND_PORT_KEY}'")
    env_file = get_env_file_path()
    if not env_file:
        return None

    _set_env_var_in_file(env_file, ENV_PORT_KEY, str(safe_port) if safe_port is not None else None)
    return env_file


def write_backend_host_env_file(host: str | None) -> Path | None:
    """Write `WECHAT_TOOL_HOST` into a `.env` file so `uv run main.py` picks it up on restart."""

    safe_host = None if host is None else _validate_host(host, source=f"'{BACKEND_HOST_KEY}'")
    env_file = get_env_file_path()
    if not env_file:
        return None

    _set_env_var_in_file(env_file, ENV_HOST_KEY, safe_host)
    return env_file


def write_mcp_token_env_file(token: str | None) -> Path | None:
    """Write `WECHAT_TOOL_MCP_TOKEN` into a `.env` file so `uv run main.py` picks it up."""

    safe_token = None if token is None else _validate_mcp_token(token, source=f"'{MCP_TOKEN_KEY}'")
    env_file = get_env_file_path()
    if not env_file:
        return None

    _set_env_var_in_file(env_file, ENV_MCP_TOKEN_KEY, safe_token)
    return env_file
