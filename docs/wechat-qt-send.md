# Windows WeChat 4.x send adapter

Verification environment: Windows, WeChat (Weixin) 4.1.15.13 with a Chinese interface.

## Cause and implementation

The WeChat main-window class is `Qt51514QWindowIcon`. Win32 child-window enumeration exposes only the rendering window, with no separate Edit HWND. UI Automation exposes the input field as an Edit control with AutomationId `chat_input_field`. Its Name includes a voice-input hint when empty, so that field cannot establish conversation identity; the adapter instead checks the conversation title's exact name.

After the shared lock-screen, process, window, and activation checks, `WeChatBridge` explicitly selects the Qt adapter `WeChatQtUI` by window class. The existing non-Qt path remains; Qt failure does not fall back to blind keystroke sending.

The Qt flow is:

1. Write the target name into the search field through ValuePattern and verify it by reading it back.
2. Accept only local conversation search results whose names match exactly; duplicate names, including off-screen results, produce an error.
3. Click the result control's UIA coordinates, verify that the conversation title exactly matches the target, and locate the fixed input control inside the chat panel.
4. Check the existing draft, focus the input, write the body through ValuePattern, and read it back character for character.
5. Locate the send button inside `chat_message_page`, recheck the target and body, and click once.
6. Return success only after observing an empty input and a new matching message element in the message list.

On this machine, Invoke on both search results and the send button had returned success without performing the action. Both controls are therefore located through UIA and clicked at their coordinates, followed by verification of the visible result. This is an explicit adaptation strategy, rather than a resend fallback after Invoke failure. ValuePattern writes the message body; the adapter neither uses global Ctrl+V / Alt+S nor changes the clipboard.

The send button and input both belong to `chat_message_page`. `content_view.bottom_ui_` is a placeholder container in a sibling area, rather than an ancestor of the send button. The test tree follows this structure observed on the machine.

Qt can destroy controls during enumeration while the interface changes. Only `UIA_E_ELEMENTNOTAVAILABLE (0x80040201)` triggers a fresh query, with a Warning recorded; this remains bounded by the bridge's `send_timeout_s` (default 10 seconds). Other COM errors surface directly. Reference: [Microsoft UI Automation error codes](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-error-codes).

## Errors and result semantics

- `WECHAT_SESSION_AMBIGUOUS`: duplicate-name results; the adapter does not select the first result and send.
- `WECHAT_DRAFT_CONFLICT`: a different draft already exists in WeChat; it is neither overwritten nor appended to.
- `WECHAT_INPUT_WRITE_FAILED`: the text interface is not writable, or readback differs.
- `WECHAT_SEND_UNCONFIRMED`: sending was attempted but the result is unconfirmed; the project's draft is retained, and the existing three-second duplicate-prevention window restarts when this uncertain result returns. There is no automatic resend.
- `WECHAT_UIA_ERROR`: UIA initialization or control operation failed; the backend retains the full exception chain and operation stage.

Success means only that the local WeChat client processed the submission and displayed a new message; it is not a WeChat server delivery receipt. The current GUI routes by display name and cannot switch accounts or uniquely identify recipients by wxid.

Automatic reply is off by default. Enabling it requires the user to confirm that the data account matches the currently logged-in WeChat account, explicitly select the window, and authorize the reply scope. During operation, it binds the process ID, process start time, and window handle, locates conversations by full name, and uses client-visible evidence to confirm local submission. These checks do not establish the live login account, recipient ID, or identity of a new self-sent database message; do not switch WeChat accounts while it runs.

Duplicate names, changed names, or different drafts pause the affected conversation. Unknown submission results retain records for manual checking, without automatic retries or resending old batches. Application restart does not automatically re-enable replies: the window and account require confirmation again. The complete automatic-reply flow still requires separate real-model and WeChat hardware acceptance. Support is not claimed for every WeChat version, other interface languages, or environments with screen-reader interfaces disabled.

## Use and verification

The added Windows dependency `uiautomation==2.0.29` is recorded in `pyproject.toml` and `uv.lock`, and installed in this workspace's `.venv`. Restart the source-development application to load this code; on other machines, run `uv sync --no-editable` using the repository's existing procedure. Installers require rebuilding; the Windows packaging script now collects the UIA dependency.

Regression command, from the project root:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_wechat_qt_ui.py tests/test_wechat_ui_bridge.py tests/test_ui_bridge_adversarial.py tests/test_ui_bridge_challenger.py tests/test_chat_send_api.py tests/test_chat_send_adversarial.py -q -p no:cacheprovider
```

Local verification without sending covered input recognition, switching between two conversations, Chinese/newline/emoji draft writing and exact readback in File Transfer Assistant, and draft restoration. A standalone minimal PyInstaller executable verified UIA initialization and real input recognition in a frozen environment; this is not full installer acceptance.

2026-09-30 verification record:

- All 145 relevant backend regression tests passed, including 20 Qt adapter tests; 29 frontend input-component tests passed.
- With user authorization, the real code sent one test message to File Transfer Assistant; the input cleared and a new message appeared.
- That real send exposed a false receipt classification caused by the voice hint appended to the empty input's Name. A regression reproduced the issue, which was corrected. Subsequent read-only hardware inspection confirmed `input_found=true` and `draft_empty=true`.
- With additional user authorization, real `WeChatBridge.send_message` sent one receipt-verification message to File Transfer Assistant at 09:58. Its exact Chinese body is represented by Unicode escapes below. The call returned `success=true`, `content_length=17`, `duration_ms=900.24`, and process exit code 0.

  ```text
  \u53d1\u9001\u56de\u6267\u9a8c\u8bc1 2026-09-30
  ```

- One identical message already existed before sending, and one new message appeared afterward with the input cleared. The final code correctly confirmed success despite the voice hint in the input's Name. This verified real sending and the local receipt, rather than a server delivery receipt.
