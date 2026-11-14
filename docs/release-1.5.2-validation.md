# v1.5.2 release validation

Validation date: 2026-11-14. Build platform: Windows x64.

## Status and scope

The static frontend and frozen backend were built successfully from the staged v1.5.2 source. The complete desktop Node suite passed both before and after fresh locked desktop dependency installation. The frontend test command remains **red**: its Node tests passed, but Vitest reported the failures listed below. A successful static build is not a passing frontend test suite.

The assembled packaged application passed the focused launch, migration and restart checks described below. Installer payload checks passed; the installer wizard and an installed upgrade were not executed. Frontend failures remain explicitly recorded below. Remote publication is verified separately after upload.

The release changes frontend development-server readiness and verified-snapshot output-path relocation. See [the source impact and regression record](startup-output-migration-release-impact-20261114.md) for the implementation scope and existing-data repair boundary.

## Source and version review

The staged source was compared with the downloaded GitHub main snapshot at commit `1a05eb062eaee0cdb44b55e28ba1d52ed21eb3ce`.

These six files contain only the release-version change from `1.5.1` to `1.5.2`:

| File | Version-only difference |
| --- | --- |
| `README.md` | Current project version sentence. |
| `pyproject.toml` | `[project].version`. |
| `uv.lock` | The editable `xwechat` package version. |
| `src/wechat_decrypt_tool/__init__.py` | `__version__`. |
| `desktop/package.json` | Root `version`. |
| `desktop/package-lock.json` | Root `version` and `packages[""].version`. |

All other dependency declarations and lockfile values in those files match the baseline. The Python lock contains 162 package records; no dependency-resolution record changed. `uv lock --check --offline` exited successfully and resolved the existing 162-package lock without changes.

All **550 original frontend files** match the baseline byte for byte, including its package manifest and lockfile. Generated output and installed dependencies are not counted as source files in that comparison.

## Desktop regression suite

Before installing fresh desktop dependencies, the complete staged suite was run with the already installed dependency directory:

```powershell
cd desktop
node --test
```

Result: **159 tests passed; zero failed, cancelled, or skipped**. This result includes the existing repository tests and the new startup-readiness and snapshot-migration regressions. It is not a fresh-dependency or packaged-application result.

## Frontend installation, tests, and static output

Runtime: Node `v24.22.2`, npm `11.19.1`.

```powershell
cd frontend
npm ci --offline --no-audit --no-fund
npm test
npm run generate
cd ..
node desktop/scripts/copy-ui.cjs
```

- Offline installation completed successfully and added 637 packages.
- The frontend Node tests passed: **85/85**.
- Vitest exited with failure: **83 passed and 19 failed files out of 102**; **1,068 passed and 31 failed tests out of 1,099**, plus **14 suite-load failures**. Suite-load failures are listed separately; they are not 14 additional executed test failures.
- Static generation completed successfully with **43 prerendered routes**.
- Generated public output: **289 files, 8,028,577 bytes**.
- Copied desktop UI: **295 files, 8,036,529 bytes**, including six existing image/document-icon assets outside the generated tree. Every generated file matched its copied file; no mismatches were found.
- The generated text scan found no build-path leaks.

The build reported two lifecycle scripts not approved by npm's `allowScripts` policy, chunk-size/timing and annotation warnings, and a Nitro Windows file-URL cache-driver externalization warning. Installation and static prerender still completed; no Nitro server runtime is included in the copied static UI.

### Frontend failure classification

These are observed test-run symptoms, not confirmed product runtime defects. The unchanged frontend source establishes that no frontend source edit was made to obtain either these failures or the successful static build. It does not prove that every assertion is invalid or that the application paths covered by the failing tests work correctly.

| Observed failure | Files | Interpretation and limit |
| --- | --- | --- |
| Vite denied a raw import of a Python source file outside the frontend root. | 1 | An observed test-loader filesystem restriction. It stopped the suite before tests executed. |
| Module loading rejected a `file:///assets/images/wechat/` URL as an invalid Windows absolute filename. | 13 | An observed Windows asset-import/loading problem in the test harness. Runtime UI behavior has not been inferred from it. |
| `vi.spyOn()` received an undefined value rather than a function. | 4 | An observed test-double/setup failure. A mismatch between the tests and their mounted environment is an inference, not a confirmed root cause. |
| The focus-restoration assertion expected the model-change button, but `document.activeElement` was the body in happy-dom. | 1 | An observed DOM assertion failure. Whether it is harness behavior or a product focus defect requires runtime investigation. |

### Suite-load failures: exact file identifiers

These 14 files did not reach test execution:

- `frontend/tests/ai-presets.test.js`
- `frontend/tests/anti-revoke-message-status.test.js`
- `frontend/tests/chat-copy-errors.test.js`
- `frontend/tests/chat-input-workspace.test.js`
- `frontend/tests/chat-insights-ui.test.js`
- `frontend/tests/chat-message-rendering.test.js`
- `frontend/tests/emoji-download-recovery.test.js`
- `frontend/tests/finder-card.test.js`
- `frontend/tests/library-message-entry.test.js`
- `frontend/tests/message-recognition-ui.test.js`
- `frontend/tests/video-local-preview.test.js`
- `frontend/tests/voice-transcription-local.test.js`
- `frontend/tests/voice-transcription-message.test.js`
- `frontend/tests/chat-cached-transients.test.js`

### Executed test failures: exact identifiers

The following 31 test names are reproduced verbatim from the test output, including source-language identifiers and parameterized values.

#### `frontend/tests/application-backup-context.test.js` (1 failed tests)

- `changing only the export account never changes the real application store or erases a library draft`

#### `frontend/tests/application-backup.test.js` (2 failed tests)

- `keeps the completed report open if navigation to restored data is rejected`
- `does not discard another account draft by switching accounts without the user choosing to proceed`

#### `frontend/tests/auto-reply-history.test.js` (22 failed tests)

- `历史固定每页五条，下一页展示后端对应五条范围`
- `只有四种终态提供删除入口，确认后按编码ID删除本程序记录`
- `活动与待核对记录不允许删除`
- `取消确认不发出删除或其他写入请求`
- `删除失败明确显示原始错误并保留记录，不自动重试`
- `删除末页唯一记录后回到有效上一页，保留五条分页`
- `删除进行中禁用筛选、分页与删除，重复点击不重复提交`
- `删除成功后读取失败明确报错，用户刷新可恢复剩余记录`
- `删除响应迟到不能修改新账号记录，也不继续刷新旧账号`
- `不采用账号、记录ID或删除结果不符的响应：{"id":"first","account":"b","deleted":true}`
- `不采用账号、记录ID或删除结果不符的响应：{"id":"other","account":"a","deleted":true}`
- `不采用账号、记录ID或删除结果不符的响应：{"id":"first","account":"a","deleted":false}`
- `一键删除跨越所有分页和会话，不受筛选影响，保留活动记录与其他账号`
- `取消一键删除不发请求、不改变记录`
- `一键删除失败保留记录、展示错误且不自动重试`
- `一键删除进行中阻止重复提交，迟到响应不修改新账号`
- `一键删除拒绝账号或数量无效的响应：{"account":"b","deleted_count":1}`
- `一键删除拒绝账号或数量无效的响应：{"account":"a","deleted_count":-1}`
- `一键删除拒绝账号或数量无效的响应：{"account":"a","deleted_count":"1"}`
- `一键删除拒绝账号或数量无效的响应：{"account":"a","deleted_count":1.5}`
- `一键删除拒绝账号或数量无效的响应：{"account":"a"}`
- `没有可删除记录时显示真实零结果并保留待核对记录`

#### `frontend/tests/library-page.test.js` (5 failed tests)

- `does not discard an edited saved item when a navigation request is declined`
- `asks once before replacing an edited item with an explicitly selected saved source`
- `back to list retains dirty report when discard is declined`
- `removes the deleted item from the interface while preserving the cleanup failure`
- `removes the deleted folder from the interface while preserving the cleanup failure`

#### `frontend/tests/local-search.test.js` (1 failed tests)

- `模型管理使用二级弹窗并恢复焦点`

## Frozen backend build and provenance

Build runtime: Python **3.11.16**, PyInstaller **6.22.3**. The existing installed build/voice dependency environment was reused without installation or synchronization. `UV_PROJECT_ENVIRONMENT` selected that environment; `PYTHONPATH` selected the staged source. No personal environment path is required in the release instructions.

From the staged repository root, with `UV_PROJECT_ENVIRONMENT` already pointing to the installed dependency environment:

```powershell
$env:UV_NO_SYNC = '1'
$env:UV_FROZEN = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = Join-Path (Get-Location).Path 'src'
node desktop/scripts/build-backend.cjs
```

The existing build script completed successfully and produced `desktop/resources/backend/wechat-backend.exe` (**267,172,255 bytes**), together with its native dependencies.

- The executable's Windows `FileVersion` and `ProductVersion` both report **1.5.2.0**.
- The embedded application package reports `__version__ = "1.5.2"`.
- All **200 embedded application modules** were extracted from the frozen archive and bytecode-compared with the staged source compiled at the same optimization level. All matched; none were missing or different. This checks actual artifact provenance rather than relying only on the build command's import path.
- The build's packaged smoke test returned `ok: true`, `frozen: true`, platform `win32`, and title `xwechat`.

A second frozen smoke run and a normal backend launch used an empty isolated data/output directory, without the source checkout on `PYTHONPATH`:

- `--smoke-backend` passed with `frozen: true`.
- `/api/health` returned HTTP **200**, `status: "healthy"`, and service `xwechat`.
- `/openapi.json` returned HTTP **200**, title `xwechat`, and application version **1.5.2**.
- The isolated backend process was stopped after verification.

PyInstaller reported missing `fastmcp`, `onnx`, and `tensorrt` optional imports, plus a missing `tzdata` hidden import. Its ctypes scan also warned about UI Automation DLL names, but both corresponding x86/x64 DLLs were confirmed in the executable archive. Frozen startup passed; these checks do not exercise every optional feature.

## Backend-only check boundaries

The following assembled-application section records the subsequent focused packaged acceptance and artifact hash checks. Installer-wizard and full-application acceptance remain separate.

No full Python test suite, exhaustive frontend runtime regression, real model inference, real WeChat operation, or message send was performed as part of these build checks. The frontend test failures above remain unresolved and must not be described as a passing full test suite.

## Final desktop installation and packaged checks

A direct post-install asset download for ffmpeg-static timed out. The locked 301-package desktop dependency tree was then installed offline without lifecycle scripts. Electron 45.2.1 was materialized from the existing official cache ZIP after its SHA256 matched the package vendor checksum and its ZIP CRC passed. FFmpeg 6.1.1 and its license/readme files were copied only after matching the corresponding bytes in the checksum-verified previous installer. No dependency resolution changed. electron-builder was 26.18.0 and ffmpeg-static was 5.3.0.

With that real Electron runtime, the complete desktop command passed again: **159 tests, zero failures**. This included the SNS WASM JPEG fixture using Electron run-as-node. The numeric-token roundtrip for `9007199254740993` was exact.

The existing builder assembled unsigned NSIS and portable ZIP artifacts with explicit Electron 45.2.1 and the verified cache ZIP. No signing certificate was used.

### Focused packaged acceptance

- Launched the actual packaged executable with a newly created, separately owned profile and a private replica of one verified snapshot containing 21 databases. No account key, AI store, or runtime settings were copied. The replica was marked as a supported manual import so it did not connect to live WeChat or auto-start snapshot refresh.
- Observed the actual chat interface. Backend health and account reads succeeded. The actual renderer user agent identified xwechat 1.5.2 and Electron 45.2.1.
- Invoked the existing renderer desktop bridge `setOutputDir` through the local debugging protocol, reaching the real main-process IPC/migration/backend restart path. This was not a DOM mutation, replacement migration implementation, or mocked success. Native settings-button/folder-picker automation was not completed.
- Confirmed migration success, one switchable account before and after, and snapshot refresh unavailable for the manual-import replica. Closed and relaunched the same packaged executable; the migrated path persisted and account reads succeeded again.
- All 21 replica database hashes and original database hashes remained unchanged. Exactly 65 manifest output-path fields changed; every other JSON value and numeric token was identical. The original legacy desktop settings hash remained unchanged. Acceptance application/backend/debugging ports were closed afterward.

### Payload and privacy checks

The installer, ZIP and tested unpacked application share 383 byte-identical common files; installer and unpacked application additionally share `resources/elevate.exe`. ZIP CRC verification passed. Runtime-created output-location helper files in the unpacked directory were not present in uploaded assets. The packaged migration/main/renderer-startup modules match staged source; desktop and backend executable resources report version 1.5.2.

Scoped inventory and pattern checks covered the installer/ZIP, ASAR, generated UI text and 200 frozen application modules. No tested personal-state filename or known personal path/account/build-root probe matched. All inspected application code filenames were relative. This is not a guarantee that every possible private value was detected.

## Release asset hashes

| Asset | SHA256 |
| --- | --- |
| `xwechat-1.5.2-Setup.exe` | `da2b0bd5c151501adfa1f9b6cabf1aeac9c0d0e68e3ca2278d6fc01b8e4c42fe` |
| `xwechat-1.5.2-Setup.zip` | `b93dc399a3931aaafdf7260fe9aa4630e0cf023df225cd17a0f7fb260b96175a` |
| `xwechat-1.5.2-Setup.exe.blockmap` | `2363856b247ff48b8296224fa1ff1e1d3d7adb521eb8a1923f82ef50e5fb045c` |

## Remaining boundaries

The complete frontend test suite is not passing. The native settings-button/folder-picker path, installer wizard, installed upgrade/uninstall, power loss, all features/accounts/formats, real message sending, model inference and GPU execution were not accepted by these focused checks. Earlier incorrectly migrated metadata is not silently repaired by updating the application. Existing affected data needs a separate evidence-preserving repair; snapshot-registry validation remains enforced.
