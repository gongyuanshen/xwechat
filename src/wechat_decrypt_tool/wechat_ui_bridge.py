"""
src/wechat_decrypt_tool/wechat_ui_bridge.py
===========================================
Zero-Injection Windows UI Automation Bridge for WeChat Client.
Provides non-intrusive desktop automation, status probing, clipboard safety,
rate limiting, and fail-fast exception handling adhering to Debug-First Policy.
"""
from __future__ import annotations

import asyncio
import ctypes
from dataclasses import dataclass
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import stat
import sys
import threading
import time
from typing import Any, Callable, Literal, Optional, Union

from PIL import Image, UnidentifiedImageError
import psutil

if sys.platform == "win32":
    import win32api
    import win32clipboard
    import win32con
    import win32gui
    import win32process
else:
    win32api = None
    win32clipboard = None
    win32con = None
    win32gui = None
    win32process = None

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Strong-Typed Exception Hierarchy (Let-It-Fail)
# ---------------------------------------------------------------------------
class WeChatBridgeError(Exception):
    """Base class for all WeChat UI Automation bridge exceptions."""

    code: str = "WECHAT_BRIDGE_ERROR"
    status_code: int = 500
    detail: str = "微信桌面桥接服务异常"

    def __init__(
        self,
        detail: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        if detail is not None:
            self.detail = detail
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        super().__init__(f"[{self.code}] {self.detail} (HTTP {self.status_code})")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "status_code": self.status_code,
            "detail": self.detail,
        }


class WeChatProcessNotFoundError(WeChatBridgeError):
    code: str = "WECHAT_NOT_RUNNING"
    status_code: int = 503
    detail: str = "未检测到运行中的微信进程，请确认微信客户端已启动"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail=detail if detail is not None else self.detail)


class ControlledSendError(WeChatBridgeError):
    """Persistable boundary evidence; an intent is uncertain even before Click."""

    def __init__(self, detail: str, *, code: str, status_code: int, stage: str,
                 submission_intended: bool, submission_attempted: bool) -> None:
        super().__init__(detail, code=code, status_code=status_code)
        self.stage = stage
        self.submission_intended = submission_intended
        self.submission_attempted = submission_attempted

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "stage": self.stage,
                "submission_intended": self.submission_intended,
                "submission_attempted": self.submission_attempted}


class WeChatWindowNotFoundError(WeChatBridgeError):
    code: str = "WECHAT_WINDOW_NOT_FOUND"
    status_code: int = 503
    detail: str = "未找到微信主窗口(WeChatMainWndForPC)，请确认微信主界面未完全退出或关闭"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail=detail if detail is not None else self.detail)


class WeChatLockedError(WeChatBridgeError):
    code: str = "WECHAT_LOCKED"
    status_code: int = 423
    detail: str = "微信处于锁定状态，请在电脑端解锁微信后重试"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail=detail if detail is not None else self.detail)


class WeChatSessionNotFoundError(WeChatBridgeError):
    code: str = "WECHAT_SESSION_NOT_FOUND"
    status_code: int = 404
    detail: str = "未找到指定联系人或群聊会话，请确认会话名称准确或已存在于聊天列表"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail=detail if detail is not None else self.detail)


class WeChatInputBoxNotFoundError(WeChatBridgeError):
    code: str = "WECHAT_INPUT_BOX_NOT_FOUND"
    status_code: int = 502
    detail: str = "未定位到微信消息输入框控件，可能界面被遮挡、处于非聊天界面或UI结构不匹配"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail=detail if detail is not None else self.detail)


class WeChatSendTimeoutError(WeChatBridgeError):
    code: str = "WECHAT_SEND_TIMEOUT"
    status_code: int = 504
    detail: str = "消息发送超时，微信未能及时响应发送动作"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail=detail if detail is not None else self.detail)


class WeChatRateLimitExceededError(WeChatBridgeError):
    code: str = "WECHAT_RATE_LIMIT_EXCEEDED"
    status_code: int = 429
    detail: str = "操作过于频繁或3秒内重复发送相同内容，已被风控拦截，请稍候重试"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail=detail if detail is not None else self.detail)


class WeChatClipboardLockedError(WeChatBridgeError):
    code: str = "CLIPBOARD_LOCKED"
    status_code: int = 500
    detail: str = "访问系统剪贴板超时或被拒绝，剪贴板被其他应用锁定"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail=detail if detail is not None else self.detail)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class SendReceipt:
    success: bool
    session_title: str
    content_length: int
    duration_ms: float
    timestamp: float


@dataclass(frozen=True)
class WeChatWindowBinding:
    pid: int
    process_created_at: float
    hwnd: int


@dataclass
class ControlledTextReceipt:
    account: str
    session_id: str
    target_name: str
    run_id: str
    status: str
    evidence: dict[str, Any]
    duration_ms: float
    timestamp: float




@dataclass
class FileSendReceipt:
    """Confirmed local file submission; not a server-delivery receipt."""

    success: bool
    session_title: str
    file_name: str
    file_size_bytes: int
    duration_ms: float
    timestamp: float


@dataclass
class ImageSendReceipt:
    """An exact image in a new local outgoing record, not recipient delivery."""

    success: bool
    session_title: str
    image_name: str
    image_format: str
    image_size_bytes: int
    duration_ms: float
    timestamp: float
    server_id: str
    match_kind: str
    generation: str


@dataclass
class WeChatStatus:
    running: bool
    pid: Optional[int]
    window_found: bool
    hwnd: Optional[int]
    locked: bool


@dataclass
class ClipboardBackup:
    text: Optional[str] = None
    was_empty: bool = False
    has_non_text: bool = False


# ---------------------------------------------------------------------------
# Rate Limiter & Idempotency Cache
# ---------------------------------------------------------------------------
class WeChatRateLimiter:
    """In-memory rate limiter and anti-flood protection for WeChat UI automation."""

    def __init__(
        self,
        duplicate_window_s: float = 3.0,
        session_cooldown_s: float = 1.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.duplicate_window_s = float(duplicate_window_s)
        self.session_cooldown_s = float(session_cooldown_s)
        self._clock = clock
        self._msg_cache: dict[str, float] = {}
        self._session_cache: dict[str, float] = {}

    @staticmethod
    def compute_message_key(
        session: str, content: str, *, message_kind: Literal["text", "image", "file"] = "text",
    ) -> str:
        """Keep legacy text keys; attachment fingerprints have separate namespaces."""
        if message_kind in ("image", "file"):
            payload = json.dumps([session, content], ensure_ascii=False).encode("utf-8")
            return message_kind + ":" + hashlib.sha256(payload).hexdigest()
        payload = f"{session}:{content}".encode("utf-8")
        return hashlib.md5(payload).hexdigest()

    def _prune(self, now: float) -> None:
        """Evict expired records to keep memory bounded."""
        cutoff_msg = now - self.duplicate_window_s
        cutoff_session = now - self.session_cooldown_s
        for k in [k for k, ts in self._msg_cache.items() if ts < cutoff_msg]:
            del self._msg_cache[k]
        for s in [s for s, ts in self._session_cache.items() if ts < cutoff_session]:
            del self._session_cache[s]

    def check_and_record(
        self, session: str, content: str, *, message_kind: Literal["text", "image", "file"] = "text",
    ) -> None:
        """Enforce rate limits and record timestamps atomically.

        Raises:
            WeChatRateLimitExceededError: On duplicate content within 3s or session flood within 1s.
        """
        now = self._clock()
        self._prune(now)

        # 1. Check duplicate message within 3.0 seconds
        msg_key = self.compute_message_key(session, content, message_kind=message_kind)
        last_msg = self._msg_cache.get(msg_key)
        if last_msg is not None and (now - last_msg) < self.duplicate_window_s:
            rem = self.duplicate_window_s - (now - last_msg)
            raise WeChatRateLimitExceededError(
                f"3秒内禁止向会话[{session}]重复发送相同内容（防重保护生效中，还需等待 {rem:.1f} 秒）"
            )

        # 2. Check session cooldown within 1.0 second
        last_session = self._session_cache.get(session)
        if last_session is not None and (now - last_session) < self.session_cooldown_s:
            rem = self.session_cooldown_s - (now - last_session)
            raise WeChatRateLimitExceededError(
                f"会话[{session}]发送频率超限，每秒最多发送1条消息（请等待 {rem:.1f} 秒冷却）"
            )

        self._msg_cache[msg_key] = now
        self._session_cache[session] = now

    def rollback_message(
        self, session: str, content: str, rollback_session: bool = True,
        *, message_kind: Literal["text", "image", "file"] = "text",
    ) -> None:
        """Roll back idempotency cache if send fails due to OS/UI error."""
        msg_key = self.compute_message_key(session, content, message_kind=message_kind)
        self._msg_cache.pop(msg_key, None)
        if rollback_session:
            self._session_cache.pop(session, None)

    def retain_uncertain_message(
        self, session: str, content: str, *, message_kind: Literal["text", "image", "file"] = "text",
    ) -> None:
        """Start duplicate protection at the uncertain result, not before UI waits."""
        now = self._clock()
        self._msg_cache[self.compute_message_key(session, content, message_kind=message_kind)] = now
        self._session_cache[session] = now

    def reset(self) -> None:
        """Reset internal caches (for tests and teardown)."""
        self._msg_cache.clear()
        self._session_cache.clear()


# ---------------------------------------------------------------------------
# Core Bridge Class
# ---------------------------------------------------------------------------
class WeChatBridge:
    def __init__(
        self,
        rate_limiter: Optional[WeChatRateLimiter] = None,
        lock: Optional[asyncio.Lock] = None,
        send_timeout_s: float = 10.0,
    ) -> None:
        self.psutil = psutil
        self.win32api = win32api
        self.win32clipboard = win32clipboard
        self.win32con = win32con
        self.win32gui = win32gui
        self.win32process = win32process
        self.user32 = ctypes.windll.user32 if sys.platform == "win32" and hasattr(ctypes, "windll") else None

        self.rate_limiter = rate_limiter or WeChatRateLimiter()
        self._lock = lock
        self._images_in_flight: set[tuple[str, str]] = set()
        self.send_timeout_s = float(send_timeout_s)

    def _get_lock(self) -> asyncio.Lock:
        """Lazy lock initialization bound to the current running event loop."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._lock is None or (
            current_loop is not None and getattr(self._lock, "_loop", None) not in (None, current_loop)
        ):
            self._lock = asyncio.Lock()
        return self._lock

    def _is_workstation_locked(self) -> bool:
        """Check if Windows desktop / workstation is locked (Win+L / Secure Desktop)."""
        if sys.platform != "win32" or not self.user32:
            return False
        try:
            hdesk = self.user32.OpenInputDesktop(0, False, 0x01FF)
            if not hdesk:
                return True
            try:
                self.user32.SetThreadDesktop(hdesk)
            finally:
                if hasattr(self.user32, "CloseDesktop"):
                    self.user32.CloseDesktop(hdesk)
            return False
        except Exception:
            return True

    def _ensure_desktop_access(self) -> None:
        """Ensure current thread desktop is attached to active input desktop."""
        if sys.platform == "win32" and self.user32:
            try:
                hdesk = self.user32.OpenInputDesktop(0, False, 0x01FF)
                if hdesk:
                    try:
                        self.user32.SetThreadDesktop(hdesk)
                    finally:
                        if hasattr(self.user32, "CloseDesktop"):
                            self.user32.CloseDesktop(hdesk)
            except Exception:
                pass

    def _find_main_process_pid(self) -> Optional[int]:
        """Fast process probe (wechat.exe / weixin.exe), shortest cmdline main process."""
        candidates: list[tuple[int, int]] = []
        for proc in self.psutil.process_iter(["pid", "name"]):
            try:
                name = proc.info.get("name") if hasattr(proc, "info") and isinstance(proc.info, dict) else proc.name()
                if name and name.lower() in ("wechat.exe", "weixin.exe"):
                    try:
                        cmdline = proc.cmdline() if hasattr(proc, "cmdline") else []
                    except Exception:
                        cmdline = []
                    cmdline_str = " ".join(cmdline).lower() if cmdline else ""
                    if "--type=" not in cmdline_str:
                        candidates.append((len(cmdline_str), proc.pid))
            except Exception:
                continue
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    def _find_main_window_hwnd(self, pid: Optional[int] = None) -> Optional[int]:
        """Select the real main window, excluding Qt history and media windows."""
        if not self.win32gui:
            return None
        self._ensure_desktop_access()

        # 1. Try WeChat 3.x standard window class
        try:
            hwnd = self.win32gui.FindWindow("WeChatMainWndForPC", None)
            if hwnd and self.win32gui.IsWindow(hwnd):
                if pid is not None and self.win32process:
                    try:
                        _, win_pid = self.win32process.GetWindowThreadProcessId(hwnd)
                        if win_pid == pid:
                            return hwnd
                    except Exception:
                        pass
                elif pid is None:
                    return hwnd
        except Exception:
            pass

        # Qt history and image windows share the native QWindowIcon class.
        # Its UIA provider class, not enumeration order, identifies the main UI.
        candidates: list[int] = []
        main_windows: list[int] = []

        def enum_cb(h: int, _extra: Any) -> None:
            if not self.win32gui.IsWindow(h):
                return
            if pid is not None and self.win32process.GetWindowThreadProcessId(h)[1] != pid:
                return
            native_class = self.win32gui.GetClassName(h)
            if native_class == "WeChatMainWndForPC":
                main_windows.append(h)
            elif "qwindowicon" in native_class.lower():
                candidates.append(h)

        try:
            self.win32gui.EnumWindows(enum_cb, None)
            if candidates:
                import uiautomation as auto
                with auto.UIAutomationInitializerInThread():
                    main_windows.extend(hwnd for hwnd in candidates
                                        if auto.ControlFromHandle(hwnd).ClassName == "mmui::MainWindow")
        except Exception as exc:
            raise WeChatBridgeError("无法核验微信主窗口身份，请查看后台异常日志",
                                    code="WECHAT_WINDOW_PROBE_FAILED", status_code=502) from exc
        if len(main_windows) > 1:
            raise WeChatBridgeError("当前微信进程存在多个主窗口，无法唯一定位",
                                    code="WECHAT_WINDOW_AMBIGUOUS", status_code=409)
        return main_windows[0] if main_windows else None

    def _check_window_locked(self, hwnd: int) -> bool:
        """Check if window title or child windows indicate locked state."""
        if not hwnd or not self.win32gui:
            return False
        try:
            title = self.win32gui.GetWindowText(hwnd) or ""
            if "已锁定" in title or "locked" in title.lower():
                return True
        except Exception:
            pass

        locked = False

        def child_cb(h: int, _extra: Any) -> None:
            nonlocal locked
            try:
                t = self.win32gui.GetWindowText(h) or ""
                if "已锁定" in t or "locked" in t.lower():
                    locked = True
            except Exception:
                pass

        try:
            self.win32gui.EnumChildWindows(hwnd, child_cb, None)
        except Exception:
            pass
        return locked

    def _is_window_hung(self, hwnd: int) -> bool:
        """Check if the WeChat window is frozen or hung (Let-it-fail)."""
        if sys.platform != "win32" or not self.user32 or not hwnd:
            return False
        try:
            return bool(self.user32.IsHungAppWindow(hwnd))
        except Exception:
            return False

    def _locate_input_box(self, hwnd: int, session_title: str) -> bool:
        """Legacy native HWND client only; Qt uses WeChatQtUI.locate_input."""
        if not hwnd or not self.win32gui:
            return False
        if not self.win32gui.IsWindow(hwnd):
            return False
        if self.win32gui.IsIconic(hwnd):
            return False

        found_input = False
        input_class_keywords = ("edit", "richedit", "chatedit", "mmedit", "textedit", "qlineedit")

        def enum_child_cb(child_hwnd: int, _extra: Any) -> None:
            nonlocal found_input
            if found_input:
                return
            try:
                if self.win32gui.IsWindow(child_hwnd):
                    cls_name = self.win32gui.GetClassName(child_hwnd).lower()
                    if any(kw in cls_name for kw in input_class_keywords):
                        found_input = True
            except Exception:
                pass

        try:
            self.win32gui.EnumChildWindows(hwnd, enum_child_cb, None)
        except Exception:
            return False

        return found_input

    def _activate_window(self, hwnd: int) -> None:
        """Restore window if minimized, bring to foreground, and strictly verify activation."""
        if not self.win32gui or not hwnd:
            raise WeChatWindowNotFoundError("无效的微信窗口句柄，无法激活")
        self._ensure_desktop_access()

        if self.win32gui.IsIconic(hwnd):
            self.win32gui.ShowWindow(hwnd, win32con.SW_RESTORE if self.win32con else 9)
            time.sleep(0.05)

        # 1. AttachThreadInput to bypass Windows foreground lock
        if self.win32process and self.win32api and self.win32gui:
            try:
                fg = self.win32gui.GetForegroundWindow()
                fore_thread = self.win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
                app_thread = self.win32api.GetCurrentThreadId()
                if fore_thread and fore_thread != app_thread:
                    self.win32process.AttachThreadInput(app_thread, fore_thread, True)
                    try:
                        self.win32gui.BringWindowToTop(hwnd)
                        self.win32gui.SetForegroundWindow(hwnd)
                    finally:
                        self.win32process.AttachThreadInput(app_thread, fore_thread, False)
            except Exception as exc:
                logger.debug("AttachThreadInput activate attempt: %s", exc)

        # 2. Direct SetForegroundWindow & SwitchToThisWindow fallback
        try:
            self.win32gui.SetForegroundWindow(hwnd)
        except Exception as exc:
            logger.warning("SetForegroundWindow failed for hwnd=%s: %s", hwnd, exc)
            if sys.platform == "win32":
                try:
                    ctypes.windll.user32.SwitchToThisWindow(hwnd, True)
                except Exception:
                    pass

        # 4. Verification with short polling (up to 200ms) to allow DWM transition
        # Handles transient GetForegroundWindow() == 0 during animation
        activated = False
        last_fg = 0
        for _ in range(5):
            fg_hwnd = self.win32gui.GetForegroundWindow()
            last_fg = fg_hwnd
            if fg_hwnd == hwnd:
                activated = True
                break
            try:
                if self.win32con and self.win32gui.GetAncestor(fg_hwnd, getattr(self.win32con, "GA_ROOT", 2)) == hwnd:
                    activated = True
                    break
            except Exception:
                pass
            time.sleep(0.04)

        if not activated:
            raise WeChatBridgeError(
                f"无法将微信窗口(hwnd={hwnd})激活至前台(当前前台hwnd={last_fg})，消息发送中止",
                code="ACTIVATION_FAILED",
                status_code=500,
            )

    def _backup_clipboard(self) -> ClipboardBackup:
        """Read current CF_UNICODETEXT or format state with retry loop (Let-it-fail).

        Raises:
            WeChatClipboardLockedError: If clipboard cannot be opened after retries.
        """
        if not self.win32clipboard or not self.win32con:
            return ClipboardBackup(text=None, was_empty=True, has_non_text=False)

        last_exc: Optional[Exception] = None
        opened = False
        for _ in range(5):
            try:
                self.win32clipboard.OpenClipboard()
                opened = True
                break
            except Exception as exc:
                last_exc = exc
                time.sleep(0.02)

        if not opened:
            raise WeChatClipboardLockedError(
                f"无法访问系统剪贴板进行备份，剪贴板被其他应用锁定: {last_exc}"
            )

        try:
            # 1. Check Unicode text format
            if self.win32clipboard.IsClipboardFormatAvailable(self.win32con.CF_UNICODETEXT):
                text = self.win32clipboard.GetClipboardData(self.win32con.CF_UNICODETEXT)
                return ClipboardBackup(text=text, was_empty=False, has_non_text=False)

            # 2. Check non-text formats (images, files)
            has_non_text = False
            for fmt in (
                getattr(self.win32con, "CF_DIB", 8),
                getattr(self.win32con, "CF_BITMAP", 2),
                getattr(self.win32con, "CF_HDROP", 15),
            ):
                try:
                    if self.win32clipboard.IsClipboardFormatAvailable(fmt):
                        has_non_text = True
                        break
                except Exception:
                    pass

            if hasattr(self.win32clipboard, "CountClipboardFormats"):
                try:
                    if self.win32clipboard.CountClipboardFormats() > 0:
                        has_non_text = True
                except Exception:
                    pass

            if has_non_text:
                return ClipboardBackup(text=None, was_empty=False, has_non_text=True)

            return ClipboardBackup(text=None, was_empty=True, has_non_text=False)
        finally:
            self.win32clipboard.CloseClipboard()

    def _set_clipboard_text(self, text: str) -> None:
        """Write Unicode text to clipboard with bounded retry loop."""
        if not self.win32clipboard or not self.win32con:
            raise WeChatClipboardLockedError("系统剪贴板模块不可用")
        last_exc: Optional[Exception] = None
        for _ in range(5):
            try:
                self.win32clipboard.OpenClipboard()
                try:
                    self.win32clipboard.EmptyClipboard()
                    self.win32clipboard.SetClipboardData(self.win32con.CF_UNICODETEXT, text)
                    return
                finally:
                    self.win32clipboard.CloseClipboard()
            except Exception as exc:
                last_exc = exc
                time.sleep(0.02)
        raise WeChatClipboardLockedError(f"访问系统剪贴板超时或被拒绝: {last_exc}")

    def _restore_clipboard(self, backup: Optional[Union[ClipboardBackup, str]]) -> None:
        """Restore original text to clipboard with bounded retry loop (Let-it-fail).

        Raises:
            WeChatClipboardLockedError: If restoring fails after retries.
        """
        if not self.win32clipboard or not self.win32con:
            return

        text_to_restore: Optional[str] = None
        should_empty = False

        if isinstance(backup, ClipboardBackup):
            if backup.text is not None:
                text_to_restore = backup.text
                should_empty = True
            elif backup.was_empty:
                should_empty = True
            # If backup.has_non_text: should_empty is False, do NOT wipe user clipboard!
        elif isinstance(backup, str):
            text_to_restore = backup
            should_empty = True
        elif backup is None:
            # None passed without explicit empty confirmation: NEVER wipe out clipboard
            return

        if not should_empty and text_to_restore is None:
            return

        last_exc: Optional[Exception] = None
        restored = False
        for _ in range(5):
            try:
                self.win32clipboard.OpenClipboard()
                try:
                    self.win32clipboard.EmptyClipboard()
                    if text_to_restore is not None:
                        self.win32clipboard.SetClipboardData(self.win32con.CF_UNICODETEXT, text_to_restore)
                    restored = True
                    return
                finally:
                    self.win32clipboard.CloseClipboard()
            except Exception as exc:
                last_exc = exc
                time.sleep(0.02)

        if not restored:
            raise WeChatClipboardLockedError(
                f"无法恢复系统剪贴板，剪贴板被其他应用锁定: {last_exc}"
            )

    def _trigger_paste(self) -> None:
        """Send Ctrl + V keystroke to paste clipboard content."""
        if not self.win32api or not self.win32con:
            return
        try:
            self.win32api.keybd_event(self.win32con.VK_CONTROL, 0, 0, 0)
            time.sleep(0.01)
            try:
                self.win32api.keybd_event(ord("V"), 0, 0, 0)
                time.sleep(0.01)
            finally:
                self.win32api.keybd_event(ord("V"), 0, self.win32con.KEYEVENTF_KEYUP, 0)
        finally:
            self.win32api.keybd_event(self.win32con.VK_CONTROL, 0, self.win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.02)

    def _trigger_send_shortcut(self) -> None:
        """Send Alt + S shortcut to trigger message sending."""
        if not self.win32api or not self.win32con:
            return
        try:
            self.win32api.keybd_event(self.win32con.VK_MENU, 0, 0, 0)
            time.sleep(0.01)
            try:
                self.win32api.keybd_event(ord("S"), 0, 0, 0)
                time.sleep(0.01)
            finally:
                self.win32api.keybd_event(ord("S"), 0, self.win32con.KEYEVENTF_KEYUP, 0)
        finally:
            self.win32api.keybd_event(self.win32con.VK_MENU, 0, self.win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.02)

    def _switch_to_session(self, session_title: str) -> None:
        """Focus search box via Ctrl+F, paste target session_title, and press Enter to switch."""
        if not self.win32api or not self.win32con:
            return

        # 1. Focus search box: Ctrl + F
        try:
            self.win32api.keybd_event(self.win32con.VK_CONTROL, 0, 0, 0)
            time.sleep(0.01)
            try:
                self.win32api.keybd_event(ord("F"), 0, 0, 0)
                time.sleep(0.01)
            finally:
                self.win32api.keybd_event(ord("F"), 0, self.win32con.KEYEVENTF_KEYUP, 0)
        finally:
            self.win32api.keybd_event(self.win32con.VK_CONTROL, 0, self.win32con.KEYEVENTF_KEYUP, 0)

        time.sleep(0.02)

        # 2. Paste target session_title into search box
        self._set_clipboard_text(session_title)
        self._trigger_paste()
        time.sleep(0.03)

        # 3. Press Enter to select the target chat session
        try:
            self.win32api.keybd_event(self.win32con.VK_RETURN, 0, 0, 0)
            time.sleep(0.01)
        finally:
            self.win32api.keybd_event(self.win32con.VK_RETURN, 0, self.win32con.KEYEVENTF_KEYUP, 0)

        time.sleep(0.05)

    def probe_status(self) -> WeChatStatus:
        """Probe WeChat process and main window status (Let-it-fail)."""
        if self._is_workstation_locked():
            pid = self._find_main_process_pid()
            hwnd = self._find_main_window_hwnd(pid) if pid else None
            return WeChatStatus(
                running=bool(pid),
                pid=pid,
                window_found=bool(hwnd and self.win32gui and self.win32gui.IsWindow(hwnd)),
                hwnd=hwnd,
                locked=True,
            )

        pid = self._find_main_process_pid()
        if not pid:
            return WeChatStatus(running=False, pid=None, window_found=False, hwnd=None, locked=False)

        hwnd = self._find_main_window_hwnd(pid)
        if not hwnd or not (self.win32gui and self.win32gui.IsWindow(hwnd)):
            return WeChatStatus(running=True, pid=pid, window_found=False, hwnd=None, locked=False)

        locked = self._check_window_locked(hwnd)
        return WeChatStatus(running=True, pid=pid, window_found=True, hwnd=hwnd, locked=locked)

    def _prepare_send_window(self) -> int:
        """Shared desktop/window preflight; callers own their rate reservation."""
        if self._is_workstation_locked():
            raise WeChatLockedError("Windows桌面处于锁屏状态，无法投递按键与消息")
        pid = self._find_main_process_pid()
        if not pid:
            raise WeChatProcessNotFoundError()
        hwnd = self._find_main_window_hwnd(pid)
        if not hwnd or not (self.win32gui and self.win32gui.IsWindow(hwnd)):
            raise WeChatWindowNotFoundError()
        if self._check_window_locked(hwnd):
            raise WeChatLockedError("微信客户端处于锁定状态，请在电脑端解锁微信后重试")
        if self._is_window_hung(hwnd):
            raise WeChatSendTimeoutError("微信主窗口无响应，发送超时")
        self._activate_window(hwnd)
        return hwnd

    def list_send_windows(self) -> list[WeChatWindowBinding]:
        """Read-only candidates for explicit selection; no login identity claim."""
        if sys.platform != "win32":
            raise WeChatBridgeError("自动回复仅支持 Windows 微信 Qt 客户端",
                                    code="WECHAT_CONTROLLED_SEND_UNSUPPORTED", status_code=501)
        if self._is_workstation_locked():
            raise WeChatLockedError()
        import uiautomation as auto

        candidates = []
        def inspect(hwnd, _extra):
            if not self.win32gui.IsWindow(hwnd):
                return
            if not self.win32gui.GetClassName(hwnd).lower().startswith("qt"):
                return
            pid = self.win32process.GetWindowThreadProcessId(hwnd)[1]
            process = psutil.Process(pid)
            if process.name().lower() not in ("wechat.exe", "weixin.exe"):
                return
            if auto.ControlFromHandle(hwnd).ClassName == "mmui::MainWindow":
                candidates.append(WeChatWindowBinding(pid, process.create_time(), hwnd))
        try:
            with auto.UIAutomationInitializerInThread():
                self.win32gui.EnumWindows(inspect, None)
        except Exception as exc:
            raise WeChatBridgeError("无法枚举微信发送窗口，请检查进程和 UIA 状态",
                                    code="WECHAT_WINDOW_PROBE_FAILED", status_code=502) from exc
        return candidates

    def validate_window_binding(self, binding: WeChatWindowBinding) -> None:
        """Validate only the selected process/window, never search a replacement."""
        if self._is_workstation_locked():
            raise WeChatLockedError()
        try:
            valid = (self.win32gui.IsWindow(binding.hwnd)
                     and self.win32process.GetWindowThreadProcessId(binding.hwnd)[1] == binding.pid
                     and psutil.Process(binding.pid).create_time() == binding.process_created_at
                     and self.win32gui.GetClassName(binding.hwnd).lower().startswith("qt"))
        except (psutil.Error, OSError) as exc:
            raise WeChatBridgeError("绑定的微信进程或窗口已失效，请重新确认账号并启用",
                                    code="WECHAT_WINDOW_BINDING_CHANGED", status_code=409) from exc
        if not valid:
            raise WeChatBridgeError("绑定的微信进程或窗口已变化，请重新确认账号并启用",
                                    code="WECHAT_WINDOW_BINDING_CHANGED", status_code=409)
        import uiautomation as auto
        with auto.UIAutomationInitializerInThread():
            if auto.ControlFromHandle(binding.hwnd).ClassName != "mmui::MainWindow":
                raise WeChatBridgeError("绑定窗口已不是微信主窗口",
                                        code="WECHAT_WINDOW_BINDING_CHANGED", status_code=409)
        if self._check_window_locked(binding.hwnd):
            raise WeChatLockedError()

    async def send_controlled_text(self, *, account: str, session_id: str, target_name: str,
                                   content: str, run_id: str, binding: WeChatWindowBinding,
                                   verify_before_submit: Callable[[], None],
                                   persist_submission: Callable[[], None],
                                   check_before_send=None) -> ControlledTextReceipt:
        """One controlled attempt. Durable task ownership belongs to the caller.

        Verification runs under the async lock and again in the UI worker;
        persist_submission runs in the worker and must atomically claim the
        pending task and reject any second claim. Account and chat IDs
        label data ownership, not identities read from the WeChat window.
        """
        fields = (account, session_id, target_name, content, run_id)
        if any(not isinstance(value, str) or not value.strip() for value in fields):
            raise WeChatBridgeError("受控发送需要账号、会话 ID、完整名称、正文和运行代次",
                                    code="INVALID_PAYLOAD", status_code=400)
        if (not isinstance(binding, WeChatWindowBinding) or binding.pid <= 0
                or binding.hwnd <= 0 or binding.process_created_at <= 0
                or not callable(verify_before_submit) or not callable(persist_submission)):
            raise WeChatBridgeError("受控发送窗口绑定或提交回调无效", code="INVALID_PAYLOAD", status_code=400)
        cancelled = threading.Event()
        started = time.monotonic()
        async with self._get_lock():
            verify_before_submit()
            if check_before_send is not None:
                await check_before_send()
            self.rate_limiter.check_and_record(target_name, content)

            def operation():
                ui = None
                def verify():
                    if cancelled.is_set():
                        raise WeChatBridgeError("受控发送已取消，未提交",
                                                code="AUTO_REPLY_CANCELLED", status_code=409)
                    verify_before_submit()
                    self.validate_window_binding(binding)
                try:
                    import uiautomation as auto
                    from .wechat_qt_ui import WeChatQtUI
                    with auto.UIAutomationInitializerInThread():
                        verify()
                        if self._is_window_hung(binding.hwnd):
                            raise WeChatSendTimeoutError("绑定的微信窗口无响应")
                        self._activate_window(binding.hwnd)
                        ui = WeChatQtUI(binding.hwnd, auto, self.send_timeout_s)
                        evidence = ui.send_text(target_name, content, window_api=self.win32gui,
                                                verify_before_submit=verify, persist_submission=persist_submission)
                        self.validate_window_binding(binding)
                        return evidence
                except Exception as exc:
                    intended = ui is not None and ui.submission_intended
                    attempted = ui is not None and ui.submission_attempted
                    stage = ui.stage if ui is not None else "initialize"
                    if intended or attempted:
                        self.rate_limiter.retain_uncertain_message(target_name, content)
                    else:
                        self.rate_limiter.rollback_message(target_name, content)
                    logger.exception("Controlled WeChat text failed: hwnd=%s stage=%s intended=%s attempted=%s",
                                     binding.hwnd, stage, intended, attempted)
                    uncertain = intended or attempted
                    raise ControlledSendError(
                        "发送结果待核对，请勿重发" if uncertain else str(exc),
                        code="WECHAT_SEND_UNCONFIRMED" if uncertain else (
                            exc.code if isinstance(exc, WeChatBridgeError) else "WECHAT_UIA_ERROR"),
                        status_code=504 if uncertain else (
                            exc.status_code if isinstance(exc, WeChatBridgeError) else 502),
                        stage=stage, submission_intended=intended, submission_attempted=attempted,
                    ) from exc

            evidence = await self._await_operation(operation, _cancel_callback=cancelled.set)
        return ControlledTextReceipt(account, session_id, target_name, run_id, "client_displayed", evidence,
                                     round((time.monotonic() - started) * 1000, 2), time.time())

    async def send_message(self, session_title: str, content: str) -> SendReceipt:
        """
        Send text message to target WeChat session:
        1. Entry validation & fail-fast
        2. Anti-flood rate limiting
        3. Single-flight lock acquisition
        4. Workstation / desktop lock verification
        5. WeChat process & window probe
        6. Client lock & hung verification
        7. Window activation & foreground verification
        8. Qt: exact UIA session selection, draft readback, send and local receipt verification;
           legacy HWND: clipboard backup, session navigation, paste, Alt+S and restoration
        9. Return SendReceipt with execution timing
        """
        # 1. Centralized Entry Validation (Fail-Fast)
        session = str(session_title or "").strip()
        if not session:
            raise WeChatSessionNotFoundError("会话名称不能为空")
        if content is None or not str(content).strip():
            raise WeChatBridgeError("发送消息内容不能为空", code="INVALID_PAYLOAD", status_code=400)

        # 2. Rate Limiting (Fails fast before lock)
        self.rate_limiter.check_and_record(session, content)

        # 3. Concurrency Serialization
        t_start = time.perf_counter()
        async with self._get_lock():
            try:
                hwnd = self._prepare_send_window()
            except WeChatBridgeError:
                self.rate_limiter.rollback_message(session, content)
                raise

            # Explicit Qt adapter; an UIA error must never fall back to blind keys.
            if self.win32gui.GetClassName(hwnd).lower().startswith("qt"):
                self._send_qt_message(hwnd, session, content)
            else:
                # Legacy native-window client keeps its existing clipboard path.
                backup = self._backup_clipboard()
                try:
                    self._switch_to_session(session)
                    if not self._locate_input_box(hwnd, session):
                        raise WeChatInputBoxNotFoundError()
                    self._set_clipboard_text(content)
                    self._trigger_paste()
                    self._trigger_send_shortcut()
                except Exception:
                    self.rate_limiter.rollback_message(session, content)
                    raise
                finally:
                    self._restore_clipboard(backup)

        duration_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
        return SendReceipt(
            success=True,
            session_title=session,
            content_length=len(content),
            duration_ms=duration_ms,
            timestamp=time.time(),
        )




    @staticmethod
    def _validate_image_path(image_path: str) -> tuple[Path, str]:
        """Decode a real JPEG/PNG at the send boundary and fingerprint its bytes."""
        if not isinstance(image_path, str) or not image_path.strip():
            raise WeChatBridgeError("图片路径不能为空", code="WECHAT_IMAGE_PATH_INVALID", status_code=400)
        path = Path(image_path)
        if (not path.is_absolute() or path.is_reserved() or "\x00" in image_path
                or image_path.replace("/", "\\").startswith("\\\\.\\")):
            raise WeChatBridgeError("图片路径必须为普通文件的本地绝对路径", code="WECHAT_IMAGE_PATH_INVALID", status_code=400)
        try:
            path = path.resolve(strict=True)
            if not stat.S_ISREG(path.stat().st_mode):
                raise WeChatBridgeError("图片路径必须指向普通文件", code="WECHAT_IMAGE_PATH_INVALID", status_code=400)
            data = path.read_bytes()
        except FileNotFoundError as exc:
            raise WeChatBridgeError("图片文件不存在", code="WECHAT_IMAGE_FILE_NOT_FOUND", status_code=404) from exc
        except (OSError, ValueError) as exc:
            raise WeChatBridgeError("图片文件无法读取，请检查路径和文件权限", code="WECHAT_IMAGE_FILE_UNREADABLE", status_code=400) from exc
        expected_format = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG"}.get(path.suffix.lower())
        if expected_format is None:
            raise WeChatBridgeError("仅支持 JPG、JPEG 或 PNG 图片", code="WECHAT_IMAGE_FORMAT_UNSUPPORTED", status_code=400)
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format != expected_format:
                    raise WeChatBridgeError("图片真实格式与扩展名不一致，或不是 JPEG/PNG", code="WECHAT_IMAGE_FORMAT_UNSUPPORTED", status_code=400)
                image.verify()
            # Container verification alone does not reject every truncated JPEG.
            with Image.open(io.BytesIO(data)) as image:
                image.load()
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
            raise WeChatBridgeError("图片内容损坏或无法完整解码", code="WECHAT_IMAGE_INVALID", status_code=400) from exc
        return path, hashlib.sha256(data).hexdigest()

    @staticmethod
    async def _await_operation(operation, *args, _cancel_callback=None, **kwargs):
        task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                # The worker cannot be cancelled. Keep the caller's send lock
                # until its UI/snapshot operation has actually finished.
                cancelled = True
                if _cancel_callback is not None:
                    _cancel_callback()
                if task.cancelled():
                    raise
            except Exception:
                if cancelled:
                    logger.exception("WeChat operation failed while finishing a cancelled request")
                    raise asyncio.CancelledError from None
                raise
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def send_image(self, session_title: str, image_path: str, *,
                         account: str, username: str) -> ImageSendReceipt:
        """Submit once and confirm exact local evidence for the target account/chat."""
        session = str(session_title or "").strip()
        if not session:
            raise WeChatSessionNotFoundError("会话名称不能为空")
        path, fingerprint = await asyncio.to_thread(self._validate_image_path, image_path)
        image_key = (session, fingerprint)
        if image_key in self._images_in_flight:
            raise WeChatRateLimitExceededError("同一图片仍在发送或核对中，请勿重复提交")
        self.rate_limiter.check_and_record(session, fingerprint, message_kind="image")
        self._images_in_flight.add(image_key)
        submitted = False
        started = time.monotonic()
        try:
            async with self._get_lock():
                try:
                    from .image_send_confirmation import ImageSendConfirmation
                    try:
                        confirmation = await self._await_operation(ImageSendConfirmation.prepare, account, username, path)
                        await self._await_operation(confirmation.capture)
                        logger.info("Image send preparation completed before window activation: duration_ms=%.1f",
                                    (time.monotonic() - started) * 1000)
                    except Exception as exc:
                        logger.exception("Image confirmation baseline failed before submission")
                        raise WeChatBridgeError(f"发送前无法准备图片确认：{exc}",
                            code="WECHAT_IMAGE_CONFIRMATION_UNAVAILABLE", status_code=409) from exc
                    foreground_started = time.monotonic()
                    hwnd = self._prepare_send_window()
                    if not self.win32gui.GetClassName(hwnd).lower().startswith("qt"):
                        raise WeChatBridgeError("图片发送目前仅适配 Windows 微信 4.x Qt 客户端",
                                                code="WECHAT_IMAGE_SEND_UNSUPPORTED", status_code=501)
                except Exception:
                    self.rate_limiter.rollback_message(session, fingerprint, message_kind="image")
                    raise
                def before_submit():
                    try:
                        confirmation.revalidate()
                    except Exception as exc:
                        raise WeChatBridgeError(f"发送前无法固定图片确认基线：{exc}",
                            code="WECHAT_IMAGE_CONFIRMATION_UNAVAILABLE", status_code=409) from exc

                self._send_qt_image(hwnd, session, str(path), fingerprint, before_submit=before_submit)
                submitted = True
                logger.info("Image send foreground submission completed: duration_ms=%.1f",
                            (time.monotonic() - foreground_started) * 1000)
                self.rate_limiter.retain_uncertain_message(session, fingerprint, message_kind="image")
                try:
                    record = await self._await_operation(confirmation.confirm, timeout_s=15)
                    # Source validation also proves it was not replaced while confirming.
                    confirmed_path, confirmed_fingerprint = await asyncio.to_thread(self._validate_image_path, str(path))
                    if confirmed_fingerprint != fingerprint:
                        raise ValueError("待发送图片在确认期间发生变化")
                    with Image.open(confirmed_path) as image:
                        image_format = image.format
                    receipt = ImageSendReceipt(True, session, path.name, image_format,
                        path.stat().st_size, (time.monotonic() - started) * 1000, time.time(),
                        record.server_id, record.match_kind, record.generation)
                except Exception as exc:
                    self.rate_limiter.retain_uncertain_message(session, fingerprint, message_kind="image")
                    logger.exception("Image was submitted but exact local confirmation failed")
                    raise WeChatBridgeError(f"图片已提交，但无法确认对应的本地出站记录：{exc}；请在微信核对，避免重复发送",
                        code="WECHAT_SEND_UNCONFIRMED", status_code=504) from exc
                self.rate_limiter.retain_uncertain_message(session, fingerprint, message_kind="image")
                logger.info("Image local outgoing record confirmed: server_id=%s generation=%s match=%s duration_ms=%.1f",
                            record.server_id, record.generation, record.match_kind, receipt.duration_ms)
                return receipt
        except asyncio.CancelledError:
            if submitted:
                self.rate_limiter.retain_uncertain_message(session, fingerprint, message_kind="image")
            else:
                self.rate_limiter.rollback_message(session, fingerprint, message_kind="image")
            raise
        finally:
            self._images_in_flight.discard(image_key)

    def _send_qt_image(self, hwnd: int, session: str, image_path: str, fingerprint: str, *, before_submit: Callable[[], None]) -> None:
        def verify_source() -> None:
            try:
                with Path(image_path).open("rb") as source:
                    current = hashlib.file_digest(source, "sha256").hexdigest()
            except (OSError, ValueError) as exc:
                raise WeChatBridgeError("待发送图片已变动或无法读取，未点击发送；请重新选择并核对微信草稿",
                                        code="WECHAT_IMAGE_SOURCE_CHANGED", status_code=409) from exc
            if current != fingerprint:
                raise WeChatBridgeError("待发送图片内容已变更，未点击发送；请重新选择并核对微信草稿",
                                        code="WECHAT_IMAGE_SOURCE_CHANGED", status_code=409)

        ui = None
        try:
            import uiautomation as auto
            from .wechat_qt_ui import WeChatQtUI

            with auto.UIAutomationInitializerInThread():
                ui = WeChatQtUI(hwnd, auto, self.send_timeout_s)
                ui.send_image(session, image_path, window_api=self.win32gui, process_api=self.win32process,
                              verify_source=verify_source, before_submit=before_submit)
        except Exception as exc:
            attempted = ui is not None and ui.submission_attempted
            if attempted:
                self.rate_limiter.retain_uncertain_message(session, fingerprint, message_kind="image")
            else:
                self.rate_limiter.rollback_message(session, fingerprint, message_kind="image")
            stage = ui.stage if ui is not None else "initialize"
            logger.exception("WeChat image UIA failed: hwnd=%s stage=%s submitted=%s", hwnd, stage, attempted)
            if isinstance(exc, WeChatBridgeError) and (not attempted or exc.code == "WECHAT_SEND_UNCONFIRMED"):
                raise
            raise WeChatBridgeError(
                f"微信图片 UI Automation 操作失败（阶段: {stage}）；请检查客户端状态和后台异常日志"
                + ("；发送结果不确定，请勿直接重发" if attempted else ""),
                code="WECHAT_SEND_UNCONFIRMED" if attempted else "WECHAT_UIA_ERROR",
                status_code=504 if attempted else 502,
            ) from exc
        logger.info("WeChat image submission observed: hwnd=%s; waiting for exact local evidence", hwnd)

    @staticmethod
    def _validate_file_path(file_path: str) -> tuple[Path, int, str]:
        """Validate an ordinary readable file and stream its content fingerprint."""
        if not isinstance(file_path, str) or not file_path.strip():
            raise WeChatBridgeError("文件路径不能为空", code="WECHAT_FILE_PATH_INVALID", status_code=400)
        path = Path(file_path)
        if (not path.is_absolute() or path.is_reserved() or "\x00" in file_path
                or file_path.replace("/", "\\").startswith("\\\\.\\")):
            raise WeChatBridgeError("文件路径必须为普通文件的本地绝对路径", code="WECHAT_FILE_PATH_INVALID", status_code=400)
        try:
            path = path.resolve(strict=True)
            if not stat.S_ISREG(path.stat().st_mode):
                raise WeChatBridgeError("文件路径必须指向普通文件", code="WECHAT_FILE_PATH_INVALID", status_code=400)
            if path.suffix.lower() in (".jpg", ".jpeg", ".png"):
                raise WeChatBridgeError("JPG/JPEG/PNG 请使用图片发送接口", code="WECHAT_FILE_PATH_INVALID", status_code=400)
            with path.open("rb") as source:
                metadata = os.fstat(source.fileno())
                if not stat.S_ISREG(metadata.st_mode):
                    raise WeChatBridgeError("文件路径必须指向普通文件", code="WECHAT_FILE_PATH_INVALID", status_code=400)
                fingerprint = hashlib.file_digest(source, "sha256").hexdigest()
        except FileNotFoundError as exc:
            raise WeChatBridgeError("文件不存在", code="WECHAT_FILE_NOT_FOUND", status_code=404) from exc
        except (OSError, ValueError) as exc:
            raise WeChatBridgeError("文件无法读取，请检查路径和文件权限", code="WECHAT_FILE_UNREADABLE", status_code=400) from exc
        return path, metadata.st_size, fingerprint

    async def send_file(self, session_title: str, file_path: str) -> FileSendReceipt:
        """Send one local file through the Qt client using the shared send lock."""
        session = str(session_title or "").strip()
        if not session:
            raise WeChatSessionNotFoundError("会话名称不能为空")
        # Arbitrarily large files must not block the event loop or create a reservation
        # until hashing finishes. Cancelling this await has no UI or limiter side effects.
        path, file_size, fingerprint = await asyncio.to_thread(self._validate_file_path, file_path)
        self.rate_limiter.check_and_record(session, fingerprint, message_kind="file")
        t_start = time.perf_counter()
        try:
            async with self._get_lock():
                try:
                    hwnd = self._prepare_send_window()
                    if not self.win32gui.GetClassName(hwnd).lower().startswith("qt"):
                        raise WeChatBridgeError(
                            "文件发送目前仅适配 Windows 微信 4.x Qt 客户端",
                            code="WECHAT_FILE_SEND_UNSUPPORTED", status_code=501,
                        )
                except Exception:
                    self.rate_limiter.rollback_message(session, fingerprint, message_kind="file")
                    raise
                self._send_qt_file(hwnd, session, str(path), fingerprint)
        except asyncio.CancelledError:
            # After reserving, lock acquisition is the only cancellable await.
            self.rate_limiter.rollback_message(session, fingerprint, message_kind="file")
            raise
        return FileSendReceipt(
            success=True, session_title=session, file_name=path.name, file_size_bytes=file_size,
            duration_ms=round((time.perf_counter() - t_start) * 1000.0, 2), timestamp=time.time(),
        )

    def _send_qt_file(self, hwnd: int, session: str, file_path: str, fingerprint: str) -> None:
        ui = None
        try:
            import uiautomation as auto
            from .wechat_qt_ui import WeChatQtUI

            with auto.UIAutomationInitializerInThread():
                ui = WeChatQtUI(hwnd, auto, self.send_timeout_s)
                ui.send_file(session, file_path, window_api=self.win32gui, process_api=self.win32process)
        except Exception as exc:
            attempted = ui is not None and ui.submission_attempted
            if attempted:
                self.rate_limiter.retain_uncertain_message(session, fingerprint, message_kind="file")
            else:
                self.rate_limiter.rollback_message(session, fingerprint, message_kind="file")
            stage = ui.stage if ui is not None else "initialize"
            logger.exception("WeChat file UIA failed: hwnd=%s stage=%s submitted=%s", hwnd, stage, attempted)
            if isinstance(exc, WeChatBridgeError) and (not attempted or exc.code == "WECHAT_SEND_UNCONFIRMED"):
                raise
            raise WeChatBridgeError(
                f"微信文件 UI Automation 操作失败（阶段: {stage}）；请检查客户端状态和后台异常日志"
                + ("；发送结果不确定，请勿直接重发" if attempted else ""),
                code="WECHAT_SEND_UNCONFIRMED" if attempted else "WECHAT_UIA_ERROR",
                status_code=504 if attempted else 502,
            ) from exc

    def _send_qt_message(self, hwnd: int, session: str, content: str) -> None:
        ui = None
        try:
            import uiautomation as auto
            from .wechat_qt_ui import WeChatQtUI

            with auto.UIAutomationInitializerInThread():
                ui = WeChatQtUI(hwnd, auto, self.send_timeout_s)
                ui.send_text(session, content)
        except Exception as exc:
            attempted = ui is not None and ui.submission_attempted
            if not attempted:
                self.rate_limiter.rollback_message(session, content)
            else:
                self.rate_limiter.retain_uncertain_message(session, content)
            stage = ui.stage if ui is not None else "initialize"
            logger.exception("WeChat Qt UIA failed: hwnd=%s stage=%s submitted=%s", hwnd, stage, attempted)
            if isinstance(exc, WeChatBridgeError):
                raise
            raise WeChatBridgeError(
                f"微信 UI Automation 操作失败（阶段: {stage}）；请检查客户端状态和后台异常日志"
                + ("；发送结果不确定，请勿直接重发" if attempted else ""),
                code="WECHAT_SEND_UNCONFIRMED" if attempted else "WECHAT_UIA_ERROR",
                status_code=504 if attempted else 502,
            ) from exc
