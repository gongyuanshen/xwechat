"""Process and Uvicorn server lifecycle helpers for graceful shutdown and restarts."""

from __future__ import annotations

import ctypes
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_RESTART_WAIT_TIMEOUT_S = 30.0
DEFAULT_RESTART_POLL_INTERVAL_S = 0.15

_active_server_lock = threading.Lock()
_active_server: Any | None = None

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5
_kernel32: Any | None = None
_kernel32_lock = threading.Lock()


def _get_windows_kernel32() -> Any:
    global _kernel32
    if _kernel32 is not None:
        return _kernel32
    with _kernel32_lock:
        if _kernel32 is None:
            from ctypes import wintypes

            lib = ctypes.WinDLL("kernel32", use_last_error=True)
            lib.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            lib.OpenProcess.restype = wintypes.HANDLE
            lib.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            lib.WaitForSingleObject.restype = wintypes.DWORD
            lib.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            lib.GetExitCodeProcess.restype = wintypes.BOOL
            lib.CloseHandle.argtypes = [wintypes.HANDLE]
            lib.CloseHandle.restype = wintypes.BOOL
            _kernel32 = lib
    return _kernel32


def register_active_server(server: Any) -> None:
    """Register the currently running Uvicorn server instance for graceful shutdown."""
    global _active_server
    with _active_server_lock:
        _active_server = server


def unregister_active_server(server: Any | None = None) -> None:
    """Clear the active server registration (matching `server` if provided)."""
    global _active_server
    with _active_server_lock:
        if server is None or _active_server is server:
            _active_server = None


def get_active_server() -> Any | None:
    """Return the currently registered Uvicorn server instance, if any."""
    with _active_server_lock:
        return _active_server


@contextmanager
def active_server_scope(server: Any) -> Iterator[Any]:
    """Register `server` for the duration of the context and clear it in `finally`."""
    register_active_server(server)
    try:
        yield server
    finally:
        unregister_active_server(server)


def run_uvicorn_server(
    app: Any,
    *,
    host: str,
    port: int,
    **config_kwargs: Any,
) -> None:
    """Run a Uvicorn server registered for graceful in-process shutdown."""
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, **config_kwargs)
    server = uvicorn.Server(config)
    with active_server_scope(server):
        try:
            server.run()
        except KeyboardInterrupt:
            pass

    if not server.started:
        sys.exit(3)


def request_graceful_shutdown() -> str:
    """Request graceful shutdown of the active backend server.

    When a `uvicorn.Server` is registered, sets `server.should_exit = True` so
    Uvicorn finishes active responses, runs FastAPI lifespan shutdown, closes
    listening sockets, and exits cleanly with code 0.

    When no server is registered (for example under an external `uvicorn` CLI or
    `reload=True`), raises `SIGTERM` in-process so Uvicorn's Python signal
    handler triggers graceful shutdown without interrupting the Windows console
    group.
    """
    server = get_active_server()
    if server is not None:
        logger.info("Requesting graceful shutdown on registered Uvicorn server")
        server.should_exit = True
        return "server"

    logger.info("No active Uvicorn server registered; raising SIGTERM for graceful shutdown")
    signal.raise_signal(signal.SIGTERM)
    return "signal"


def is_process_alive(pid: int) -> bool:
    """Return True if a process with the given PID is still running."""
    try:
        target_pid = int(pid)
    except (TypeError, ValueError):
        return False
    if target_pid <= 0:
        return False

    if os.name == "nt":
        kernel32 = _get_windows_kernel32()
        handle = kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE,
            False,
            target_pid,
        )
        if not handle:
            return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
        try:
            wait_result = kernel32.WaitForSingleObject(handle, 0)
            if wait_result == _WAIT_TIMEOUT:
                return True
            if wait_result == _WAIT_OBJECT_0:
                return False
            from ctypes import wintypes

            exit_code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == _STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(target_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def can_bind_address(host: str, port: int) -> bool:
    """Return True if `(host, port)` can be bound exclusively right now."""
    try:
        target_port = int(port)
        addr = (str(host), target_port)
        family = socket.AF_INET6 if ":" in str(host) else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            sock.bind(addr)
        return True
    except OSError:
        return False


def wait_for_process_exit_and_port_available(
    old_pid: int,
    host: str,
    port: int,
    *,
    initial_delay_s: float = 0.0,
    timeout_s: float = DEFAULT_RESTART_WAIT_TIMEOUT_S,
    poll_interval_s: float = DEFAULT_RESTART_POLL_INTERVAL_S,
    is_alive_fn: Callable[[int], bool] = is_process_alive,
    can_bind_fn: Callable[[str, int], bool] = can_bind_address,
    sleep_fn: Callable[[float], object] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> bool:
    """Wait until `old_pid` has exited and `(host, port)` can be bound."""
    delay = max(0.0, float(initial_delay_s))
    timeout = max(0.0, float(timeout_s))
    poll = max(0.01, float(poll_interval_s))
    target_port = int(port)

    if delay > 0:
        sleep_fn(delay)

    deadline = monotonic_fn() + timeout
    while True:
        if not is_alive_fn(old_pid) and can_bind_fn(host, target_port):
            return True
        remaining = deadline - monotonic_fn()
        if remaining <= 0:
            return False
        sleep_fn(min(poll, remaining))


def run_delayed_restart_launcher(
    argv: Sequence[str],
    *,
    wait_fn: Callable[..., bool] = wait_for_process_exit_and_port_available,
    popen_fn: Callable[..., Any] = subprocess.Popen,
) -> int:
    """Wait for the previous backend process and port, then spawn the replacement."""
    if len(argv) < 7 or argv[5] != "--":
        raise ValueError(
            "Expected arguments: <old_pid> <host> <port> <delay_s> <timeout_s> -- <command...>"
        )

    old_pid = int(argv[0])
    host = str(argv[1]).strip()
    port = int(argv[2])
    delay_s = float(argv[3])
    timeout_s = float(argv[4])
    target = list(argv[6:])
    if not host or port < 1 or port > 65535 or not target:
        raise ValueError("Invalid restart launcher arguments")

    ready = wait_fn(
        old_pid,
        host,
        port,
        initial_delay_s=delay_s,
        timeout_s=timeout_s,
    )
    if not ready:
        logger.error(
            "Timed out waiting for old backend pid=%s to exit and %s:%s to become available",
            old_pid,
            host,
            port,
        )
        return 1

    popen_fn(target, cwd=os.getcwd(), env=os.environ.copy())
    return 0


if __name__ == "__main__":
    sys.exit(run_delayed_restart_launcher(sys.argv[1:]))
