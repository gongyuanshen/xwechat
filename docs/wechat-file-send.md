# Sending WeChat images and file attachments

Implementation and verification date: 2026-10-02 (Asia/Singapore).

## Usage and API

Both **Image** and **File** in the Windows desktop chat input support native multiple selection, and reopening a picker can append more items. Images and files share one pending-send list, with individual removal. The attachment area shows image thumbnails, names, sizes, and the target conversation. JPG/JPEG/PNG selected through the file entry also use image previews and the existing image-send API.

Click **Send attachments (N)** to send items individually in list order, without text. Each explicitly successful item is removed; the next waits 1 second to respect the existing backend conversation cooldown. Any failure stops the batch, retains the current and later items, and displays the error. Clicking again sends only remaining items. The existing 3-second same-content duplicate prevention remains active; rate-limited items are neither skipped nor automatically retried.

Cancelling selection or any read error in a selected batch preserves the original list without partial append. After an account/conversation change, the original list cannot send or append to the new target; a running queue stops before the next item. Switching away and back still requires another explicit send click. An already-submitted current item waits for its result and cannot be recalled by this queue. Closing the input component stops later items.

An uncertain send result or lost network response marks the current item for checking and prevents direct resend, append, or removal of that item. Manual **Confirm sent** clears only the corresponding item; manual **Allow retry** only unlocks its checking state. Neither automatically resumes the queue. Other unsubmitted items can still be removed individually. Text is sent separately and is neither sent nor cleared with the attachment batch.

`wechatDesktop.chooseImage()` / `chooseFile()` map to existing `dialog:chooseImage` / `dialog:chooseFile`, callable only by the main window. Cancellation returns `{canceled:true}`; both single and multiple selection return `{canceled:false,attachments:[...]}`. Each attachment includes `path`, `name`, `sizeBytes`, and `kind`; `kind=image` additionally includes `previewDataUrl`. The native layer validates and builds previews sequentially; any failure raises an error for the whole batch. Picker paths come from native dialogs. Pasted-file paths are extracted from real `File` objects by preload; there is no renderer API accepting arbitrary path strings for reading. If an old process still returns the single-attachment format, the frontend explicitly asks for restart without implicit compatibility.

Added endpoint: `POST /api/chat/send/file`:

```json
{"account":"current-account","username":"filehelper","display_name":"\u6587\u4ef6\u4f20\u8f93\u52a9\u624b","file_path":"G:\\\u8d44\u6599\\\u62a5\u544a.pdf"}
```

The example retains the Chinese File Transfer Assistant display name and sample directory/filename through Unicode escapes. Confirmed response fields are `success`, `session`, `file_name`, `file_size_bytes`, `duration_ms`, and `timestamp`. `success` must be explicit and means a matching new file card appeared in local WeChat; it does not establish server delivery or recipient download. `account` retains existing semantics: it does not switch or verify the currently logged-in WeChat account.

The file entry verifies an absolute path, existence, regular-file type, and readability, and computes streaming SHA-256 with `hashlib.file_digest`. Disk scanning runs in a thread. All three send types reuse the single-flight lock, conversation cooldown, and content-duplicate namespaces specific to each message type. Calling the file API directly with JPG/JPEG/PNG explicitly requires the image API instead. Other extensions have no whitelist, and the application adds no size limit; client limitations surface as-is.

Errors retain the global `{code, detail}` format, including `WECHAT_FILE_PATH_INVALID`, `WECHAT_FILE_NOT_FOUND`, `WECHAT_FILE_UNREADABLE`, `WECHAT_FILE_SEND_UNSUPPORTED`, `WECHAT_FILE_BUTTON_NOT_FOUND`, `WECHAT_FILE_DIALOG_NOT_FOUND`, `WECHAT_FILE_DRAFT_UNCONFIRMED`, and existing draft/conversation-change/rate-limit/unconfirmed errors. Exceptions record the call stage and whether submission was attempted. Errors after submission are consistently retained as `WECHAT_SEND_UNCONFIRMED`; neither frontend nor backend automatically retries.

## Previous single-file hardware observation and receipt rules

The test used the current Windows WeChat 4.x Qt client. The API directly called the repository's source FastAPI application and real bridge without replacing bridge dependencies. The user authorized one send of `.work/wechat-file-send-20261002.txt` to File Transfer Assistant: 139 bytes containing only test instructions.

1. The file was selected first to inspect its draft. Both ValuePattern and TextPattern contained one U+FFFC object, with 0 embedded child objects. No additional confirmation dialog appeared. Only the probe's draft was cleared, without clicking send.
2. The first API call returned `WECHAT_FILE_DIALOG_NOT_FOUND` at the native-dialog stage, with `submitted=False` in the log and no send. The observed **Open** button was `ButtonControl`; previous observation found `SplitButtonControl`. Both used `AutomationId=1`. The implementation now locates by owning dialog, ID, and those two observed types; it does not switch paths and retry after failure. The failing case was reproduced before correction and regression.
3. After cancelling the empty dialog left by that failure, the same API was called again and clicked send once. A test-file card appeared on WeChat's right side and the input cleared. This send occurred at 02:06; authorization was used and no additional send followed.
4. That run, used to discover file-card properties, still returned unconfirmed. Final receipt parsing was subsequently implemented and verified read-only against the real file card. Automated tests cover the complete final `success=true` path, but no second file was sent merely to obtain HTTP 200.

The actual file message was `ListItemControl` / `mmui::ChatBubbleItemView`. During upload, its Name was the following, represented with Unicode escapes for Chinese labels:

```text
\u6587\u4ef6
\u8fdb\u5ea6: 10%
wechat-file-send-20261002.txt
139B
\u4e0a\u4f20\u4e2d
```

After completion, the same RuntimeId had this Name:

```text
\u6587\u4ef6
wechat-file-send-20261002.txt
139B
\u5fae\u4fe1\u7535\u8111\u7248
```

An ordinary text message observed in the same read-only session was `mmui::ChatTextItemView`, distinct from a file card. Final confirmation requires the exact target conversation, an empty draft, exactly one previously nonexistent file card, the exact filename, and an observed completion format. The pre-send snapshot includes old incomplete messages so that an old upload finishing cannot be mistaken for this send. Uploading, unknown formats, failure states, old same-name cards, ordinary text, and multiple new same-name cards cannot confirm success.

The existing 10-second UI-operation deadline remains. Large files that do not finish uploading within it enter manual checking. The implementation does not cancel WeChat uploads, retry automatically, or mark incomplete uploads successful. The send button receives only one click after location; the old clipboard and legacy-client keystroke paths are not used for file sending.

## Previous single-file verification

Relevant backend regression: 304 passed.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_chat_send_api.py tests/test_chat_send_image_api.py tests/test_chat_send_file_api.py tests/test_chat_send_adversarial.py tests/test_wechat_image_bridge.py tests/test_wechat_file_bridge.py tests/test_wechat_qt_image_ui.py tests/test_wechat_qt_file_ui.py tests/test_wechat_ui_bridge.py tests/test_wechat_qt_ui.py tests/test_ui_bridge_adversarial.py tests/test_ui_bridge_challenger.py -q -p no:cacheprovider --basetemp .work\pytest-file-send-verification
```

All 14 desktop picker tests and 68 tests across four frontend file, image, and original-input suites passed; `npm run build` succeeded. Run frontend tests in `frontend` to load existing Vitest/Vue configuration. Fixtures added `EnumWindows` isolation so missing-window tests do not enumerate the real desktop.

An isolated Electron verification page also loaded the real current `preload.cjs`, `chooseChatFile`, and `MessageInputWorkspace.vue`. UIA operated a real Windows file dialog to select the same 139-byte test file. IPC returned the correct path, filename, size, and `kind=file`; the attachment area showed the target conversation and enabled **Send file**. Attachment removal, reopening the dialog, and cancellation passed, with 0 send calls throughout. This layer used an explicitly labeled verification conversation, did not connect to a real account, and does not replace the full real-account UI-to-WeChat send workflow.

Local evidence is `.work/file-ui-validation/evidence.json`, `file-selected.png`, and `file-canceled.png`; the actual WeChat send screenshot is `.work/file-send-result.png`. Verification-only Electron/Vite processes exited; the original application process was not restarted.

The installed EXE was not rebuilt. Existing desktop processes require restart to load new main-process IPC and preload methods; frontend hot reload does not update those layers.

## Current multiple-attachment verification

- Full frontend `npm test` passed: 75 Node tests and 550 Vitest tests. Of these, 83 attachment/original-input tests covered mixed append/multiple selection, item removal, cancellation and error retention, ordering and cooldown, failure after partial success, manual checking, account/conversation changes, and unmount interruption. `npm run build` succeeded with existing duplicate-import, Browserslist, CSS, and chunk-size notices.
- All 22 targeted native desktop picker tests passed. Of 215 full desktop tests, 214 passed. `Windows smoke temp directory helper restricts inherited ACLs` failed and still failed alone: `Get-Acl` could not autoload `Microsoft.PowerShell.Security`. Importing the module separately in a Node child process reproduced existing `System.Security.AccessControl.ObjectSecurity` type-data members `AuditToString/AccessToString/Sddl/Access/Group/Owner/Path`; direct PowerShell import passed. That test and ACL script were unchanged in this iteration; system-security configuration was neither bypassed nor modified. Logs: `.work/multi-desktop-tests.log` and `.work/multi-desktop-acl-repro.log`.
- Review completed image-receipt error classification: if an image whose submission was attempted subsequently raises another `WeChatBridgeError`, it also becomes `WECHAT_SEND_UNCONFIRMED`, preserving the exception chain and duplicate-prevention record as the file path does. The incorrect error code was reproduced before a minimal correction. Relevant backend regression: 305 passed, with 7 existing deprecation notices; log: `.work/multi-backend-tests.log`. The full Python repository was not tested. The review found no remaining issues within that scope.
- There was no new authorization for real WeChat sends in this iteration, so no outbound send occurred. Component and simulated-boundary tests are not described as real batch delivery verification.
- Isolated Electron native-UI verification passed: the real image entry selected `logo.png`, then the file entry selected the 139-byte text file and `zip.png` together. The list contained 3 items, with both images actually decoded and displayed. Removing the text left two images, and cancelling native selection preserved both. `sendCalls=0`; no account or send API was connected. The current component, preload, and picker were used. Evidence and screenshots: `.work/multi-attachment-ui-validation/evidence.json`, `three-attachments.png`, and `canceled-preserved.png`.

This iteration added no dependencies or backend batch API, committed no code, restarted no user application, and rebuilt no EXE. Restart the source desktop application before using the new native multiple-selection return format.

## 2026-10-02: file not sent after image — cause and fix

The user's real 09:33 operation exposed a gap in the preceding simulated tests. The Qt image adapter always raised `WECHAT_SEND_UNCONFIRMED`, even after observing an empty draft and a new image bubble; it had no reachable success return. The queue stopped at image confirmation, so the file API was never called. The previous no-remaining-issues finding came from limited automated review and did not establish successful real mixed sending.

Read-only inspection again found no sender, direction, or children on the image bubble; its rectangle spanned the whole row. Position or a new bubble alone cannot establish success. The then-existing live message reader was reused to add direction evidence, without treating decrypted historical snapshots or archives as receipts:

- The image API passes selected `account` and target `username` to the bridge. Live-connection/read preflight failure returns explicit `WECHAT_IMAGE_RECEIPT_UNAVAILABLE` before image submission.
- Database-message IDs and UI-image IDs are both refreshed immediately before clicking send. Success requires the exact conversation, an empty draft after submitting one image, exactly one new image bubble, and exactly one new outgoing image in the same account/target's live records with nonzero `server_id`. Only then can the original queue continue to the file.
- Old images, old uploads completing, incoming images, missing server IDs, and multiple candidates are not success evidence. Post-submission query errors remain `WECHAT_SEND_UNCONFIRMED`, retaining duplicate-prevention records and unsent attachments, without automatic retry.
- Review's snapshot-ordering issue was fixed: images arriving during file selection no longer count as bubbles added after this click. Concurrent post-submission images can still pause the batch; avoid concurrent sends to the same conversation from other devices. The receipt does not establish recipient download/read status or identify image content from cross-device concurrent messages.

Verification boundaries:

- The previous fixed-unconfirmed test was first changed to require success, reproducing failure in the old code, then real SQL reading was integrated. New combined regression covers route → real bridge → real Qt adapter → real SQL, isolating only external Windows/UIA and encrypted-database transport rather than mocking a success return. It covers file sending after image confirmation and stopping the queue after read failure.
- The new confirmer ran read-only against the real account, recognized the user's recent outgoing image, and verified that this old image could not confirm a new send. No new outbound send occurred; the screenshot's `1.key` was neither read nor sent.
- Full frontend `npm test`: 75 Node tests and 550 Vitest tests passed; log: `.work/image-receipt-frontend-tests.log`.
- Final send-related backend regression: 324 passed with 7 existing dependency deprecation notices; record: `.work/image-receipt-final.log`. The independently identified snapshot issue was reproduced with a failing test, fixed, and closed on recheck.
- Full Python regression did not complete. The first full run was stopped after failures, then rerun with a failure limit. In an isolated test environment without native components, `tests/test_account_archive_cross_platform.py::TestAccountArchiveCrossPlatform` tests `test_exported_account_zip_round_trips_into_new_data_directory`, `test_extracted_archive_root_and_account_directory_are_both_recognized`, and `test_tampered_archive_is_rejected_without_replacing_existing_account` failed with `wechatdb native broker executable was not found`. Execution stopped after 3 failures and 1 pass; log: `.work/image-receipt-suite-blocked.log`. Component validation and those tests were not bypassed or modified.
- The first run also failed `tests/test_admin_server_error_logging.py::TestAdminServerErrorLogging::test_get_log_file_returns_current_backend_log_path`: the test command's additional `WECHAT_TOOL_OUTPUT_DIR` overrode the test's own data directory. Removing that command-level variable allowed all 6 tests in the log-test file to pass; production code and the test were not changed to evade it.

### User real-client acceptance

1. Fully exit and restart the current source desktop application so the backend loads the fix; the installed EXE was not repackaged.
2. In File Transfer Assistant, select a small PNG/JPG and a small non-sensitive TXT, ensuring the native WeChat input has no old draft.
3. Click send once. Keep the WeChat window available, do not switch conversations, and do not send concurrently from another device.
4. Expect an image followed automatically by a file card in WeChat; the application's attachment list should clear, with each item sent once.
5. If checking is still required or attachments remain, do not retry repeatedly. Provide the error code and screenshot after checking which messages actually exist in WeChat.

## 2026-10-02: Ctrl+V image and file paste

The chat input previously had no `paste` handler. Images and files could enter the attachment list only through native pickers; the text field's default behavior accepted text alone. The Windows desktop input now supports pasted screenshots, copied image files, ordinary files, and multiple attachments in one append. Pasting first shows the pending list; sending occurs only after clicking **Send attachments**. Plain text, including textual file paths, retains browser-default paste behavior and is not parsed into attachments.

The implementation reuses existing attachment validation, previews, queue, target binding, and send APIs. `wechatDesktop.importChatAttachments(File[])` uses Electron `webUtils.getPathForFile` in preload to obtain disk-file paths. Screenshots/virtual files without paths provide their actual `ArrayBuffer` through `chat:importAttachments` for the main process to write into isolated temporary directories. PNG/JPEG retain image previews and sending; other extensions retain ordinary-file handling. No dependency, backend send API, global shortcut, or clipboard polling was added.

Read, decode, and conversation-change errors surface directly, retaining existing attachments and text without partial append, automatic sending, or retry. The main process validates the caller window, paths, and byte format, and prevents virtual filenames from traversing directories. Same-name virtual files use different paths; failed batches clean only their own temporary files. Successfully imported virtual attachments remain until normal application exit, avoiding deletion during removal or sending. Normal exit cleans only directories created by that process and never deletes original disk files. Abnormal termination may leave attachment caches.

### 2026-10-03: staging follows the output setting

Screenshots and clipboard files without original disk paths are stored from their first write under the configured `output/cache/chat-attachments/wechat-chat-paste-*/<sequence>/<original-filename>`. For example, with output set to `D:\output`, the cache is `D:\output\cache\chat-attachments`; system Temp is no longer used. Missing directories are created; an unwritable configured directory returns the actual error without changing location. Disk files retain their original paths, and sending does not additionally copy or archive them.

Cache lifetime remains process-bound: failures/unconfirmed sends retain files, and normal exit cleans batches created by that process. Import is blocked during output migration. If this run has created paste caches or is importing, migration to a different output is blocked so queued absolute paths cannot become invalid. Handle pending attachments first, then exit and reopen before migrating; selecting the same path is unaffected.

After deleting old system Temp files, queue previews may remain even though source files are gone. Before operating WeChat, the backend returns `WECHAT_IMAGE_FILE_NOT_FOUND` or `WECHAT_FILE_NOT_FOUND`; remove the item and paste again. This change only relocates storage: sending still activates WeChat and uses UI automation for conversation, attachment, and send-button selection.

Verification:

- New paste tests first reproduced 7 failures in the old component; 8 passed after integration, with 91 attachment/original-input tests passing.
- Full frontend `npm test`: 75 Node tests and 558 Vitest tests passed; `npm run build` succeeded, retaining existing duplicate-import, Browserslist, CSS, and chunk-size notices. Logs: `.work/clipboard-frontend-tests.log`, `.work/clipboard-frontend-build.log`.
- All 28 targeted desktop picker/preload tests passed; three CJS syntax checks and `git diff --check` passed. Of 221 full desktop tests, 217 passed. Four tests in `desktop/tests/windows-real-database-smoke.test.cjs` failed due to this execution environment's ACL module, DPAPI user profile, and CNG key-access capabilities; they were neither changed nor bypassed: `Windows smoke temp directory helper restricts inherited ACLs`, `Windows smoke credential helper fingerprints the DPAPI credential independently of its lease`, `Windows smoke CNG helper deletes the exact random software key`, and `Windows smoke CNG helper fails closed for a wrong-algorithm collision`.
- Isolated Electron 40 verification used the real current Vue component, preload, IPC import function, and image decoder. A PNG screenshot without a disk path and two real disk `File` objects (TXT, PNG) appended three items through `ClipboardEvent`. Thumbnail decoding, target display, text retention, individual removal, and temporary-directory cleanup passed; source files remained and `sendCalls=0`. Evidence: `.work/clipboard-ui-validation/evidence.json`; screenshot: `.work/clipboard-ui-validation/pasted-mixed.png`. The actual OS clipboard and keyboard Ctrl+V were neither changed nor tested, and no real WeChat outbound send occurred. Production `main.cjs` registration and exit hooks were inspected in source; isolated verification called the same import/cleanup implementation. Host Electron still logged system-encryption failure, without page or preload errors.

Fully exit and restart the source desktop application to load the main process and preload. The installed EXE was not repackaged in this iteration.

## 2026-10-02: image missing after revocation

The user confirmed that Ctrl+V image/file paste worked, then reported a broken image after revocation while the file card remained visible. The anti-revocation archive and ordinary message parser used different image-identity priority: ordinary parsing preferred the local resource identity in `packed_info_data`, while archival took another identity from XML first. Nonempty `extra_json` also blocked subsequent normalized-message updates to the image identity. Reconstructing the revoked message used the wrong identity and produced an image-request 404.

Real logs and an archive copy confirmed that the failing row's original packed data and its 2124-byte PNG cache still existed. The pictured `.key` file was not read. The fix is concentrated in `anti_revoke.py`: new archives prefer packed identities; parsed local identities can update existing records; initialization corrects old archives' derived `imageMd5` from retained packed data. Original messages, packed data, message identity, and revocation evidence remain unchanged. Later XML-only replay does not overwrite a parsed local identity. No image-address retries or network fallback were added.

New tests first reproduced 3 expected failures; 5 passed after the fix. Anti-revocation, query, search, revoked-message export, and image-cache regression totaled 56 passing tests plus 8 passing subcases; log: `.work/anti-revoke-image-final.log`. Isolated copies of the real failing archive/cache returned 200 and `image/png` through current formatting and image-media functions, with bytes identical to the retained cache. Evidence: `.work/anti-revoke-image-diagnosis/evidence.json`. Verification neither modified the active database nor performed a new WeChat send or revocation.

Three additional HTML image-export tests did not pass: `test_html_export_materializes_image_md5_from_packed_info_data`, `test_prefers_message_resource_md5_over_xml_md5`, and `test_falls_back_to_secondary_md5_candidate`. Initially the environment lacked native broker configuration. After specifying the installed broker, startup still exited with `Cannot create device identity: broker unavailable`; see `.work/anti-revoke-image-export.log`. Native components were not bypassed, and the full Python repository was not tested.

After fully exiting and restarting the source desktop application, the backend corrects old image identities on its first archive read. Reopening the conversation can display the image from retained cache. The EXE was not repackaged. Historical images without original data or local resources are outside the verified recovery scope.
