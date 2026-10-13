# Initial image sending: real-machine entry verification

## 2026-10-05: restoration after removing the old receipt path

For this iteration, the user prioritized verifying background sending without injection, hooks, or taking focus, and explicitly allowed restoring Windows UI automation if no reliable path was available.

- The current machine still runs Weixin 4.1.15.13. A complete bounded read-only traversal read 133 controls, with `chat_input_field` at depth 18. The earlier depth-8 search did not find the input; that result cannot establish that WeChat exposes no background controls.
- With WeChat in the background, one raw UIA `Invoke` on **Send file** returned `S_OK`, but no WeChat-owned native file dialog appeared within two seconds. Foreground state changed after the call; a subsequent read-only check found WeChat in the foreground. No file was selected, message written, or send button clicked. This path did not pass background-send acceptance.
- Restoration reused existing Qt conversation selection, native file dialogs, the shared send lock, and duplicate-prevention records. It introduced no business-DLL injection, internal WeChat function hooks, memory modification, or deleted native broker / realtime reader.
- Images are fully decoded from their actual content, with PNG/JPEG format, extension, and content-fingerprint checks. Before submission, file content, conversation, and the single-attachment draft are checked again. Submission is attempted only once.
- Current UIA cannot reliably associate image content with outgoing identity, so after submission it still returns `WECHAT_SEND_UNCONFIRMED` for the user to check in WeChat. Observing an image bubble does not construct `success=true`; automatic synchronization snapshots do not pretend to be delivery receipts for that image.
- The frontend supports existing image selection, paste, and mixed-attachment entries, calling the image or file API by type. Unconfirmed results retain the attachment and pause later items. **Confirmed sent in WeChat** clears only the corresponding local item; **Checked not sent; allow retry** unlocks it only. Neither automatically resumes or resends.
- Image sending briefly activates WeChat, and the interface displays this limitation. Restarting the source desktop application loads the new backend; installers were not rebuilt in this iteration.

Verification in this iteration: 307 backend send-related tests across 12 files, 100 relevant frontend tests, and 42 desktop attachment tests passed; independent review passed. Real-machine verification without sending used the project's `frontend/public/logo.png` in File Transfer Assistant, completing image selection, single-image draft verification, and two source-file checks. It deliberately stopped before marking submission; `submission_attempted=false`, and the test draft was cleared. Real outbound acceptance awaits explicit user authorization for the recipient and image. Verification without sending is not described as successful real sending.

The following records describe the earlier implementation and acceptance. Historical successful sends do not replace real-send acceptance for the current iteration.

Inspection date: 2026-10-01 (Asia/Singapore).

## Results

The native image-selection entry and image-draft state were verified, and desktop image selection, preview, a separate image API, and Qt UIA sending were integrated. Automated tests cover single submission and explicit uncertain-result presentation. On 2026-10-01, the user sent a desktop PNG to File Transfer Assistant through the project's desktop application and supplied a screenshot showing a new right-side image at 17:16, confirming that real send succeeded.

Current UIA still cannot automatically prove image direction. The frontend provides manual confirmation; that manual acceptance cannot establish automatic success for every future send.

During the first probe, Windows WeChat was version 4.1.15.13, matching the repository's text-send adapter documentation. The main window was restored and activated through the existing bridge. The screenshot showed the logged-in File Transfer Assistant chat. The last read-only probe through existing code returned:

```json
{
  "running": true,
  "window_found": true,
  "locked": false,
  "chat_input_found": false
}
```

UI Automation enumerated only these two levels:

```text
WindowControl: mmui::MainWindow
└─ PaneControl: MMUIRenderSubWindowHW
```

It could not read `chat_message_page`, `chat_input_field`, the search field, or the attachment button. At that point, real controls could not support image selection, sending, or receipt confirmation. This blocker cleared during the recheck after the user reopened WeChat, as recorded below.

## Completed diagnostic checks

- Verified WeChat version, main window, foreground state, and logged-in screen, excluding a hidden window or logged-out screen for this inspection.
- Queried with explicit UIA thread initialization and with an ordinary main-thread query; results matched.
- Verified that installed `uiautomation==2.0.29` uses `RawViewWalker` for enumeration; Control View filtering did not remove the chat controls.
- Called the official `AccessibleObjectFromWindow(hwnd, OBJID_CLIENT, IID_IAccessible, ...)`, which returned `S_OK`; subsequent UIA enumeration still showed only the outer window and rendering area. Successful API invocation does not establish chat-control availability.
- Temporarily subscribed to UIA focus events and queried again; the control tree did not change. The subscription was removed.
- Read the system screen-reader flag, which was off, without changing system accessibility settings. That flag alone does not establish the root cause.
- Rechecked the results through existing `WeChatBridge.probe_status()` and `WeChatQtUI.locate_input()`.

The initial probe selected no image, wrote no draft, and clicked no send button.

## Unresolved questions

The root cause of the initial WeChat process not exposing chat accessibility controls remains unknown. The results do not imply that Windows cannot send images automatically. Implementation continued after restoring control access and verifying the draft flow following image selection.

## Official references

- [Qt 5.15 Windows accessibility query handler](https://github.com/qt/qtbase/blob/5.15/src/plugins/platforms/windows/uiautomation/qwindowsuiaaccessibility.cpp#L68-L85)
- [Microsoft AccessibleObjectFromWindow](https://learn.microsoft.com/en-us/windows/win32/api/oleacc/nf-oleacc-accessibleobjectfromwindow)
- [Microsoft WM_GETOBJECT](https://learn.microsoft.com/en-us/windows/win32/winauto/wm-getobject)
- [Microsoft AddFocusChangedEventHandler](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomation-addfocuschangedeventhandler)

## Recheck after the user reopened WeChat

On 2026-10-01, the user reported opening WeChat and manually sending an image. The follow-up check found a different WeChat process and main-window handle; this does not establish the earlier root cause of missing controls.

- `chat_message_page`, `chat_input_field`, and the exact File Transfer Assistant title were readable.
- The attachment button belonged to `tool_bar_accessible.chatinput_toolbar_left_view`, with a name translated as **Send file** and class `mmui::XButton`.
- One UIA-located click opened a WeChat-owned native file dialog, class `#32770`, with a title translated as **Select file**.
- The filename field was `EditControl`, `AutomationId=1148`; the cancel button was `ButtonControl`, `AutomationId=2`.
- The dialog was cancelled without selecting a file. Later operations must recheck the foreground window and owner instead of treating any foreground window as WeChat's dialog.
- The user's manually sent image was readable as `ListItemControl`, with a name translated as **Image**, class `mmui::ChatBubbleReferItemView`. It exposed no sender, file path, or image-content properties.
- Both ValuePattern and TextPattern were readable for the empty input, with zero text length and zero embedded objects.

The user subsequently authorized one send of a specified green-bordered test image to File Transfer Assistant. It was selected through WeChat's file dialog in the exact target conversation. The open control was `SplitButtonControl`, `AutomationId=1`. After selection, ValuePattern and TextPattern each contained one U+FFFC object character, with zero embedded child objects. No extra image-confirmation dialog appeared and no automatic submission occurred. The probe removed the draft without clicking send; that authorization remained unused at this point.

## Implementation and receipt boundaries

- The native desktop dialog selects one JPG/JPEG/PNG; previews do not expose arbitrary file reading to the browser.
- `POST /api/chat/send/image` validates the local absolute path, readability, extension/actual-format agreement, and complete image decoding.
- Images and text share the send lock and conversation cooldown; images use content fingerprints for duplicate prevention.
- Qt UIA first requires an empty WeChat draft, verifies the exact conversation and foreground window, requires exactly one image object after selection, and clicks send only once.
- After sending, it checks that the image draft cleared and exactly one new image element appeared in the message list. Missing controls, draft conflicts, and uncertain results raise explicit errors without automatic retry.
- Current UIA provides no sender, original content, or server delivery information for that image. Its element spans the whole message row, so position cannot establish direction. Even if the draft clears and one image appears, the adapter returns `WECHAT_SEND_UNCONFIRMED` for manual checking instead of constructing a success receipt. Complete automatic-receipt acceptance has not passed.
- Conversation `account` retains existing text-API semantics and does not switch or confirm the currently logged-in WeChat account.

## User hardware acceptance

Restart the desktop application using this source, select File Transfer Assistant, and click **Image** to choose an explicit JPG/PNG. Check the preview and target, then click **Send image**. Real sending passes only when the corresponding new outgoing image appears in WeChat.

On failure, the selected image remains and the specific error is shown. For uncertain results, check WeChat before another submission to avoid duplicates. In this version, the image may correctly appear in WeChat while the application reports that its send direction cannot be confirmed; report whether the corresponding new right-side image appeared. The installed EXE was not rebuilt, so old installers do not include these changes.

### Handling an unconfirmed result

`WECHAT_SEND_UNCONFIRMED` appears as the yellow **Image send result needs checking** state, retaining the original error code and reason under **View reason**. While checking is pending, the image is retained and direct resend/replacement are disabled. **Confirmed sent in WeChat** clears only the local preview and refreshes the original conversation; it does not rewrite the backend receipt.

If the user confirms it was not sent, **Checked not sent; allow retry** only restores the send button without sending automatically. After conversation switching, the original conversation's image cannot be confirmed or unlocked. Ordinary image-selection, file, draft, and other failures still show their original errors. All 48 relevant frontend tests passed, covering manual confirmation without resend, target changes, failure retention, and existing text sending.

## Fix after the first send attempt in the project application

The user selected a desktop PNG in the project application and received `WECHAT_IMAGE_DIALOG_NOT_FOUND` after clicking send. WeChat's file dialog had appeared, but its filename had not been entered. Reinspection confirmed that the control types and IDs matched the existing implementation.

The original code waited only for the top-level dialog, then inspected its children immediately, misclassifying incomplete initialization as missing controls. It now waits for the filename field and open button within the existing send timeout, retaining owner checks and single-click behavior. The two delayed-loading regressions and existing Qt tests totaled 38 passing tests. The real project adapter also reached the filename-entry stage; verification deliberately stopped before entering it and cancelled the dialog without sending. The user verifies the next send from the complete desktop application.
