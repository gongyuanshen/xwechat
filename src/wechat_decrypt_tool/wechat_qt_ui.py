"""WeChat 4.x Qt controls, observed on Windows Weixin 4.1.15.13.

Qt's chat_input_field is a UIA element, not a native Edit HWND. This adapter
uses the provider's Value pattern and clicks located controls; it never falls
back to blind keystrokes. A receipt confirms local submission, not delivery.
"""
from __future__ import annotations

import time
import logging
from pathlib import Path
from typing import Callable

from comtypes import COMError

from .wechat_ui_bridge import (
    WeChatBridgeError,
    WeChatInputBoxNotFoundError,
    WeChatSessionNotFoundError,
)

logger = logging.getLogger(__name__)


def _text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


class WeChatQtUI:
    def __init__(self, hwnd: int, automation, timeout_s: float) -> None:
        self.hwnd = hwnd
        self.automation = automation
        self.root = automation.ControlFromHandle(hwnd)
        self.deadline = time.monotonic() + timeout_s
        self.submission_intended = False
        self.submission_attempted = False
        self.stage = "session"

    def _wait(self, probe, error):
        # Qt destroys/replaces elements during navigation. Re-query only the
        # documented UIA_E_ELEMENTNOTAVAILABLE transition, within timeout_s;
        # access/COM/provider failures with any other HRESULT fail immediately.
        replaced = None
        while True:
            try:
                value = probe()
            except COMError as exc:
                if exc.hresult != -2147220991:  # UIA_E_ELEMENTNOTAVAILABLE
                    raise
                if replaced is None:
                    logger.warning("WeChat Qt replaced a UIA element during stage=%s; re-querying until deadline", self.stage)
                replaced = exc
                value = None
            if value:
                return value
            if time.monotonic() >= self.deadline:
                raise error from replaced
            time.sleep(0.05)

    def _focus(self, control) -> None:
        # Use raw COM so provider failures are not swallowed by SetFocus().
        control.Element.SetFocus()
        self._wait(
            lambda: control.HasKeyboardFocus,
            WeChatBridgeError("微信输入控件未获得焦点", code="WECHAT_INPUT_FOCUS_FAILED", status_code=502),
        )

    def _write(self, control, content: str) -> None:
        pattern = control.GetValuePattern()
        if pattern is None or pattern.IsReadOnly:
            raise WeChatBridgeError("微信控件不支持文本写入", code="WECHAT_INPUT_WRITE_FAILED", status_code=502)
        if not pattern.SetValue(content, waitTime=0):
            raise WeChatBridgeError("微信控件拒绝文本写入", code="WECHAT_INPUT_WRITE_FAILED", status_code=502)
        self._wait(
            lambda: _text(pattern.Value) == _text(content),
            WeChatBridgeError("微信控件文本回读与待写入内容不一致", code="WECHAT_INPUT_WRITE_FAILED", status_code=502),
        )

    def _session_matches(self, session: str) -> bool:
        title = self.root.TextControl(Compare=lambda c, d: c.AutomationId.endswith(".current_chat_name_label"))
        return title.Exists(0) and title.Name == session

    def locate_input(self, session: str):
        """Read-only probe; no navigation, focus, typing or sending."""
        panel = self.root.GroupControl(AutomationId="chat_message_page")
        if not panel.Exists(0):
            return None
        edit = panel.EditControl(AutomationId="chat_input_field")
        # Empty Qt input Name includes its voice hint; only the chat header is
        # the session identity. Scope the stable input ID to the chat page.
        if (edit.Exists(0) and edit.IsEnabled
                and not edit.IsOffscreen and self._session_matches(session)):
            return edit
        return None

    def prepare_session(self, session: str):
        """Resolve an exact, unambiguous local search result before editing."""
        search = self.root.EditControl(ClassName="mmui::XValidatorTextEdit", Name="搜索")
        if not search.Exists(0):
            raise WeChatSessionNotFoundError("微信会话搜索框不可用（当前适配 Windows 微信 4.x 中文界面）")
        self._focus(search)
        self._write(search, session)

        def search_result():
            results = self.root.ListControl(AutomationId="search_list")
            if not results.Exists(0):
                return None
            matches = [c for c in results.GetChildren()
                       if c.ControlTypeName == "ListItemControl" and c.Name == session
                       and c.AutomationId in (f"search_item_{session}", f"search_item_function_{session}")]
            if len(matches) > 1:
                raise WeChatBridgeError("存在同名会话，无法唯一确定收件人", code="WECHAT_SESSION_AMBIGUOUS", status_code=409)
            return matches[0] if matches and matches[0].IsEnabled and not matches[0].IsOffscreen else None

        result = self._wait(search_result, WeChatSessionNotFoundError("微信搜索未返回名称完全一致的会话"))
        # On Weixin 4.1.15 the search item's Invoke may return S_OK without
        # opening the chat. Click the located item's UIA rectangle, then verify
        # the header; no fixed coordinates or blind Enter on the first result.
        result.Click(simulateMove=False, waitTime=0)
        self._wait(lambda: self._session_matches(session), WeChatSessionNotFoundError("微信当前会话与目标名称不一致"))
        return self._wait(lambda: self.locate_input(session), WeChatInputBoxNotFoundError("目标会话的 chat_input_field 不可用"))

    def _message_ids(self, message_list, content: str) -> set[tuple[int, ...]]:
        return {tuple(c.GetRuntimeId()) for c in message_list.GetChildren()
                if c.ControlTypeName == "ListItemControl" and _text(c.Name) == _text(content)}

    def _file_ids(self, message_list, file_name: str) -> set[tuple[int, ...]]:
        """Completed local file cards observed on Weixin 4.1.15.13.

        Uploading cards contain extra progress/status lines. Neither those nor
        plain text mentioning the filename constitute a completed file card.
        This is local UI evidence, not a server-delivery acknowledgement.
        """
        matches = set()
        for item in message_list.GetChildren():
            if item.ControlTypeName != "ListItemControl" or item.ClassName != "mmui::ChatBubbleItemView":
                continue
            lines = _text(item.Name).split("\n")
            if len(lines) == 4 and lines[:2] == ["文件", file_name] and lines[2] and lines[3] == "微信电脑版":
                matches.add(tuple(item.GetRuntimeId()))
        return matches

    def _attachment_draft(self, edit, kind: str) -> tuple[str, str, int]:
        value = edit.GetValuePattern()
        text = edit.GetTextPattern()
        if value is None or text is None:
            raise WeChatBridgeError("微信输入框未提供草稿核验接口",
                                    code=f"WECHAT_{kind.upper()}_DRAFT_UNCONFIRMED", status_code=502)
        return value.Value, text.DocumentRange.GetText(-1), len(text.DocumentRange.GetChildren())

    def _select_attachment(self, session: str, path: str, *, kind: str, window_api, process_api) -> None:
        """Select one path in WeChat's owned native picker; never submit a chat."""
        panel = self.root.GroupControl(AutomationId="chat_message_page")
        file_button = panel.ButtonControl(Name="发送文件")
        self._wait(lambda: file_button.Exists(0) and file_button.IsEnabled and not file_button.IsOffscreen,
                   WeChatBridgeError("微信发送文件按钮不可用",
                                     code=f"WECHAT_{kind.upper()}_BUTTON_NOT_FOUND", status_code=502))
        if window_api.GetForegroundWindow() != self.hwnd or not self.locate_input(session):
            raise WeChatBridgeError("选择附件前微信前台或会话发生变化",
                                    code="WECHAT_SEND_STATE_CHANGED", status_code=409)
        file_button.Click(simulateMove=False, waitTime=0)
        self.stage = f"{kind}_file_dialog"
        main_pid = process_api.GetWindowThreadProcessId(self.hwnd)[1]

        def owned_dialog():
            hwnd = window_api.GetForegroundWindow()
            if (hwnd and hwnd != self.hwnd and window_api.GetClassName(hwnd) == "#32770"
                    and window_api.GetWindow(hwnd, 4) == self.hwnd  # GW_OWNER
                    and process_api.GetWindowThreadProcessId(hwnd)[1] == main_pid):
                return hwnd
            return None

        dialog_hwnd = self._wait(owned_dialog, WeChatBridgeError(
            "未找到属于微信主窗口的文件选择框",
            code=f"WECHAT_{kind.upper()}_DIALOG_NOT_FOUND", status_code=502))
        dialog = self.automation.ControlFromHandle(dialog_hwnd)
        filename = dialog.EditControl(AutomationId="1148")
        # The same native Open control has been observed as Button and SplitButton;
        # bind its dialog-scoped ID and the two supported UIA types in one query.
        open_button = dialog.Control(AutomationId="1", Compare=lambda c, d:
                                     c.ControlTypeName in ("ButtonControl", "SplitButtonControl"))
        # The native dialog becomes foreground before its child controls finish
        # initialization. Wait for readiness within the existing send deadline.
        self._wait(
            lambda: owned_dialog() == dialog_hwnd
            and filename.Exists(0) and filename.IsEnabled
            and open_button.Exists(0) and open_button.IsEnabled,
            WeChatBridgeError("微信文件选择框的文件名或打开控件在超时内未就绪",
                              code=f"WECHAT_{kind.upper()}_DIALOG_NOT_FOUND", status_code=502),
        )
        self._write(filename, path)
        if (owned_dialog() != dialog_hwnd or not self._session_matches(session)
                or filename.GetValuePattern().Value != path):
            raise WeChatBridgeError("打开附件前文件选择框、路径或目标会话发生变化",
                                    code="WECHAT_SEND_STATE_CHANGED", status_code=409)
        open_button.Click(simulateMove=False, waitTime=0)


    def send_image(self, session: str, image_path: str, *, window_api, process_api,
                   verify_source: Callable[[], None], before_submit: Callable[[], None] | None = None) -> None:
        """Observe one local image submission; this is not a directional receipt."""
        edit = self.prepare_session(session)
        self.stage = "image_prepare"
        if self._attachment_draft(edit, "image") != ("", "", 0):
            raise WeChatBridgeError("微信输入框已有文字或附件草稿，请先处理后重试",
                                    code="WECHAT_DRAFT_CONFLICT", status_code=409)
        messages = self.root.ListControl(AutomationId="chat_message_list")
        if not messages.Exists(0):
            raise WeChatBridgeError("微信消息列表不可用，无法核实图片提交状态",
                                    code="WECHAT_MESSAGE_LIST_NOT_FOUND", status_code=502)
        verify_source()
        self._select_attachment(session, image_path, kind="image", window_api=window_api, process_api=process_api)
        self.stage = "image_draft"

        def prepared():
            current = self.locate_input(session)
            return current is not None and self._attachment_draft(current, "image") == ("\ufffc", "\ufffc", 0)

        self._wait(prepared, WeChatBridgeError(
            "未确认微信输入框包含一张图片；请核对客户端草稿",
            code="WECHAT_IMAGE_DRAFT_UNCONFIRMED", status_code=502))
        panel = self.root.GroupControl(AutomationId="chat_message_page")
        button = panel.ButtonControl(Name="发送")
        self._wait(lambda: button.Exists(0) and button.IsEnabled and not button.IsOffscreen,
                   WeChatBridgeError("微信发送按钮不可用", code="WECHAT_SEND_BUTTON_NOT_FOUND", status_code=502))
        messages = self.root.ListControl(AutomationId="chat_message_list")
        if not messages.Exists(0):
            raise WeChatBridgeError("发送前微信消息列表不可用", code="WECHAT_MESSAGE_LIST_NOT_FOUND", status_code=502)
        # Include all old rows, including pending uploads, immediately before
        # the click so pictures arriving during file selection are not new.
        if before_submit is not None:
            before_submit()
        verify_source()
        before = {tuple(item.GetRuntimeId()) for item in messages.GetChildren()}
        if window_api.GetForegroundWindow() != self.hwnd or not prepared():
            raise WeChatBridgeError("发送图片前微信前台、会话或草稿发生变化",
                                    code="WECHAT_SEND_STATE_CHANGED", status_code=409)
        if time.monotonic() >= self.deadline:
            raise WeChatBridgeError("图片发送准备已超时，未点击发送；请核对微信草稿",
                                    code="WECHAT_SEND_TIMEOUT", status_code=504)
        self.stage = "image_confirm"
        self.submission_attempted = True
        button.Click(simulateMove=False, waitTime=0)

        def observed():
            current = self.locate_input(session)
            current_messages = self.root.ListControl(AutomationId="chat_message_list")
            if current is None or self._attachment_draft(current, "image") != ("", "", 0) or not current_messages.Exists(0):
                return False
            pictures = {tuple(item.GetRuntimeId()) for item in current_messages.GetChildren()
                        if item.ControlTypeName == "ListItemControl" and item.Name == "图片"
                        and item.ClassName == "mmui::ChatBubbleReferItemView"}
            return len(pictures - before) == 1

        self._wait(observed, WeChatBridgeError(
            "未确认图片草稿清空及恰好一个新图片；请在微信核对结果，避免重复发送",
            code="WECHAT_SEND_UNCONFIRMED", status_code=504))

    def send_file(self, session: str, file_path: str, *, window_api, process_api) -> None:
        """Stage a local file through the same native picker used for images."""
        edit = self.prepare_session(session)
        self.stage = "file_prepare"
        if self._attachment_draft(edit, "file") != ("", "", 0):
            raise WeChatBridgeError("微信输入框已有文字或附件草稿，请先处理后重试",
                                    code="WECHAT_DRAFT_CONFLICT", status_code=409)
        messages = self.root.ListControl(AutomationId="chat_message_list")
        if not messages.Exists(0):
            raise WeChatBridgeError("微信消息列表不可用，无法核实文件发送结果",
                                    code="WECHAT_MESSAGE_LIST_NOT_FOUND", status_code=502)
        # Include unfinished old cards too: an earlier upload completing now is
        # not evidence for this submission.
        before = {tuple(item.GetRuntimeId()) for item in messages.GetChildren()}
        self._select_attachment(session, file_path, kind="file", window_api=window_api, process_api=process_api)
        self.stage = "file_draft"

        def prepared():
            current = self.locate_input(session)
            return current is not None and self._attachment_draft(current, "file") == ("\ufffc", "\ufffc", 0)

        # Observed with a 139-byte TXT in Weixin 4.1.15.13: the single file is
        # represented by one object character, with no exposed embedded children.
        self._wait(prepared, WeChatBridgeError(
            "未确认微信输入框包含一个文件；请核对客户端草稿",
            code="WECHAT_FILE_DRAFT_UNCONFIRMED", status_code=502))
        panel = self.root.GroupControl(AutomationId="chat_message_page")
        button = panel.ButtonControl(Name="发送")
        self._wait(lambda: button.Exists(0) and button.IsEnabled and not button.IsOffscreen,
                   WeChatBridgeError("微信发送按钮不可用", code="WECHAT_SEND_BUTTON_NOT_FOUND", status_code=502))
        if window_api.GetForegroundWindow() != self.hwnd or not prepared():
            raise WeChatBridgeError("发送文件前微信前台、会话或草稿发生变化",
                                    code="WECHAT_SEND_STATE_CHANGED", status_code=409)
        self.stage = "file_confirm"
        self.submission_attempted = True
        button.Click(simulateMove=False, waitTime=0)

        def confirmed():
            current = self.locate_input(session)
            current_messages = self.root.ListControl(AutomationId="chat_message_list")
            return (current is not None and self._attachment_draft(current, "file") == ("", "", 0)
                    and current_messages.Exists(0)
                    and len(self._file_ids(current_messages, Path(file_path).name) - before) == 1)

        self._wait(confirmed, WeChatBridgeError(
            "未确认文件草稿清空及对应的新文件卡片完成上传；请在微信核对，避免重复发送",
            code="WECHAT_SEND_UNCONFIRMED", status_code=504))

    def send_text(self, session: str, content: str, *,
                  verify_before_submit: Callable[[], None] | None = None,
                  persist_submission: Callable[[], None] | None = None,
                  window_api=None) -> dict:
        """Submit once and return local UI evidence, never a delivery receipt."""
        if self.submission_intended or self.submission_attempted:
            raise WeChatBridgeError("本次提交已进入发送阶段，请先核对微信，不能重复提交",
                                    code="WECHAT_SEND_ALREADY_ATTEMPTED", status_code=409)
        if window_api is None and (verify_before_submit is not None or persist_submission is not None):
            raise WeChatBridgeError("受控发送必须提供绑定窗口的前台核验接口",
                                    code="WECHAT_SEND_WINDOW_REQUIRED", status_code=400)
        edit = self.prepare_session(session)
        self.stage = "write"
        existing_value, existing_text, children = self._attachment_draft(edit, "text")
        expected_draft = (_text(content), _text(content), 0)
        if ("\ufffc" in existing_value or "\ufffc" in existing_text
                or (_text(existing_value), _text(existing_text), children)
                not in (("", "", 0), expected_draft)):
            raise WeChatBridgeError("微信输入框已有其他文字或附件草稿，请先处理后重试",
                                    code="WECHAT_DRAFT_CONFLICT", status_code=409)
        self._focus(edit)
        self._write(edit, content)

        # Input and toolbar belong to chat_message_page; bottom_ui_ is a sibling
        # placeholder, not their ancestor (verified on Weixin 4.1.15.13).
        panel = self.root.GroupControl(AutomationId="chat_message_page")
        if not panel.Exists(0):
            raise WeChatInputBoxNotFoundError("微信聊天输入面板不可用")
        button = panel.ButtonControl(Name="发送")
        self._wait(lambda: button.Exists(0) and button.IsEnabled and not button.IsOffscreen,
                   WeChatBridgeError("微信发送按钮不可用", code="WECHAT_SEND_BUTTON_NOT_FOUND", status_code=502))

        def checked_controls():
            current = self.locate_input(session)
            current_panel = self.root.GroupControl(AutomationId="chat_message_page")
            if (current is None or not current_panel.Exists(0)
                    or not current_panel.IsEnabled or current_panel.IsOffscreen
                    or (window_api is not None and window_api.GetForegroundWindow() != self.hwnd)):
                raise WeChatBridgeError("发送前微信前台、会话或控件状态发生变化，已中止",
                                        code="WECHAT_SEND_STATE_CHANGED", status_code=409)
            current_button = current_panel.ButtonControl(Name="发送")
            if not current_button.Exists(0) or not current_button.IsEnabled or current_button.IsOffscreen:
                raise WeChatBridgeError("发送前微信发送按钮状态发生变化，已中止",
                                        code="WECHAT_SEND_STATE_CHANGED", status_code=409)
            value, text, embedded = self._attachment_draft(current, "text")
            if (_text(value), _text(text), embedded) != expected_draft:
                raise WeChatBridgeError("发送前微信草稿发生变化，已中止",
                                        code="WECHAT_SEND_STATE_CHANGED", status_code=409)
            if not self.root.ListControl(AutomationId="chat_message_list").Exists(0):
                raise WeChatBridgeError("微信消息列表不可用，无法核实发送结果",
                                        code="WECHAT_MESSAGE_LIST_NOT_FOUND", status_code=502)
            if time.monotonic() >= self.deadline:
                raise WeChatBridgeError("文字发送准备已超时，未点击发送；请核对微信草稿",
                                        code="WECHAT_SEND_TIMEOUT", status_code=504)
            return current_button

        checked_controls()
        if verify_before_submit is not None:
            verify_before_submit()
            checked_controls()
        if persist_submission is not None:
            self.stage = "submit_intent"
            persist_submission()
            self.submission_intended = True
            # A stop/configuration change while persisting still prevents the
            # click, but the saved intent must remain pending verification.
            if verify_before_submit is not None:
                verify_before_submit()
        messages = self.root.ListControl(AutomationId="chat_message_list")
        if not messages.Exists(0):
            raise WeChatBridgeError("微信消息列表不可用，无法核实发送结果",
                                    code="WECHAT_MESSAGE_LIST_NOT_FOUND", status_code=502)
        before = {tuple(item.GetRuntimeId()) for item in messages.GetChildren()}
        button = checked_controls()
        # UIA enumeration can outlast a stop/configuration update. Check the
        # task and binding again after those calls, at the submit boundary.
        if verify_before_submit is not None:
            verify_before_submit()

        self.stage = "confirm"
        self.submission_attempted = True
        # A failure after this point is ambiguous; never automatically retry.
        # Weixin 4.1.15.13 also returns S_OK for the send button's Invoke without
        # submitting. Use its observed UIA rectangle as for search navigation.
        button.Click(simulateMove=False, waitTime=0)
        def confirmed():
            current = self.locate_input(session)
            current_messages = self.root.ListControl(AutomationId="chat_message_list")
            if (current is None or self._attachment_draft(current, "text") != ("", "", 0)
                    or not current_messages.Exists(0)):
                return None
            new_ids = self._message_ids(current_messages, content) - before
            if len(new_ids) == 1:
                return {"input_cleared": True, "new_message_runtime_ids": [list(next(iter(new_ids)))]}
            return None
        return self._wait(confirmed, WeChatBridgeError(
            "未确认微信输入框清空及恰好一个新同文消息；请先核对客户端，避免重复发送",
            code="WECHAT_SEND_UNCONFIRMED", status_code=504,
        ))
