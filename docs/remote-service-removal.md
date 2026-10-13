# Original project's remote-service removal record (2026-09-30)

## Connection evidence and changes

The screenshot's **Connected**, **Remaining quota**, and **Reset time** indicators came from the original project's integration with `https://wxcdn.c3o.re`. The old backend could request a token merely when reading `/api/cdn/plan`; authentication submitted the account-folder name and the contents of `global_config` and `global_config.crc`. The quota was the remote service's download allowance, rather than local file capacity. This work did not connect to that service or consume its quota.

The following complete call chains were removed:

- The WxCDN client, token/quota/redemption APIs, system switch, frontend state store, and settings components.
- Automatic and manual remote image recovery in chats, plus the MCP remote-image parameters and descriptions.
- The branch that uploaded configuration to `https://view.free.c3o.re/api/key` to obtain an image key.
- Desktop update checks, downloads, installation, automatic checks, tray menus, IPC, dialogs, update dependencies, and dedicated signing callbacks for the original project's GitHub release source.
- Web routes no longer return the home page with HTTP 200 for missing APIs; removed GET APIs explicitly return 404.

Local image lookup, decryption, caching, resolution selection, local key derivation, and memory scanning remain. Missing images return 404. Failure to obtain and verify a local key raises an explicit error; users can use memory scanning or enter the key manually. The former download button in chats was renamed **Find local image**. Databases, chat history, and existing images were preserved.

## Historical low-level dependencies (subsequently removed)

At the end of the 2026-09-30 cleanup, these implementations still remained:

- `native_core_lease.py`: the production licensing service at `https://license.fqyw.love/v1/leases` and lease-renewal heartbeats.
- `native_core_telemetry.py`: telemetry configured with native licensing.
- `desktop/src/source-native-core-bootstrap.cjs` and its Windows module: downloading and verifying pinned native components from the original project's GitHub releases.

Disconnecting these paths completely at that stage could have broken database reading and export that required online authorization or missing native components. That stage neither fabricated licenses nor bypassed native validation, so its result could not be described as completely offline or as having removed all networking to the original project's services. As of 2026-10-08, those leases, telemetry, and component downloaders had been removed from current source during the subsequent independent-data changes. The current implementation and verification boundaries are described in [Windows source development and execution](development-windows.md). WeChat media URLs, user-configured AI services, and public model downloads are outside the original project's own services removed by this work.

## Verification

The following is the historical verification record for that stage, rather than a full regression result for current source.

- Frontend `npm test`: all 75 Node tests and 483 Vitest tests passed, totaling 558 tests with a 100% pass rate.
- `npm run generate` / `build:ui`: 34 routes were prerendered successfully, and static output was synchronized to `desktop/resources/ui`. Source and static output no longer contained WxCDN, the key service, update checks, remote image recovery, or any macOS key-capture routes (`/api/macos-key-capture`, `macos_lldb_fallback`).
- Python backend and complete macOS cleanup:
  - All macOS/Darwin-specific modules from the original project were removed: 7 modules, including `macos_db_key_capture.py` and `macos_native_capture.py`; 20 macOS-specific test files; the standalone `macos-key-extractor/` tool; and `src/wechat_decrypt_tool/native/macos/`.
  - All `darwin`/`macos` platform branches and LLDB debugging fallback logic were removed from shared modules. The system was restricted to Windows (`win32`), using Windows DPAPI and CNG.
  - All 68 core unit regression tests, including platform support, WeChat process detection, memory scanning, and key validation, passed with a 100% pass rate.
  - Integration tests involving actual export, such as `test_account_archive_cross_platform.py`, depended on the native `wechatdb_broker.exe` component described in the preceding section. If that binary was absent, the relevant export tests explicitly failed as expected with `wechatdb native broker executable was not found.`; no fake result or silent bypass was introduced.
- Full desktop tests:
  - The original macOS-specific build scripts, packaging targets, code signing, and tests such as `source-native-core-bootstrap.test.cjs` and `macos-*.test.cjs` were deleted.
  - The full desktop suite ran on a Windows host (`BypassSandbox: true`): **all 193 tests passed with a 100% pass rate (193 passed, 0 failed, 0 skipped)**, taking approximately 21.8 seconds.
  - The intermittent stream-proxy failure fixes (WHATWG restricted-port filtering and rebinding to a safe temporary port), and Windows security/cryptographic checks (removing inherited ACL permissions, CurrentUser DPAPI credential protection/decryption, and Windows CNG software-key creation/deletion), continued to pass with a 100% pass rate.

Logs are available at `.work/remote-services-media-regression.log`, `.work/remote-services-frontend.log`, `.work/remote-services-generate.log`, and `.work/remote-services-desktop-final.log`. No real WeChat account was verified, and the installed EXE was neither repackaged nor replaced. Source and static-resource changes do not automatically replace old code in running processes.

## Desktop test failure tracking

- **Current failures: 0**. All 193/193 desktop tests passed. The original macOS-only `source-native-core-bootstrap.test.cjs` test was physically removed and disconnected together with the macOS implementation; the recorded Windows target-platform test coverage was 100%.
