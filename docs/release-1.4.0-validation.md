# v1.4.0 Release Validation Record

This record is project development documentation. GitHub Release notes include only features, downloads, and necessary usage instructions.

## Release Contents

Search and attachment integration, application-data backup and direct restoration of missing items, matter status and reminders, topic versions and Time Machine, plus related privacy masking and retrieval fixes. Windows x64 offers an NSIS installer and portable ZIP. The application version and numeric Windows version are `1.4.0` and `1.4.0.0`, respectively.

## 2026-10-13 Verification

| Layer | Results and scope |
|---|---|
| Related backend regressions | 212 checks passed across 14 files, covering backup/restore, account isolation, material versions, attachment text, matters, search, and Time Machine |
| Full frontend regressions | 85 Node tests; 1,244 checks passed across 101 Vitest files |
| Full desktop regressions | 144 checks passed |
| Dependencies and builds | `uv lock --check` passed; complete icon, static UI, PyInstaller, NSIS, and ZIP builds exited with 0 |
| Package audit | PE versions matched; 168 ASAR entries contained only expected source and dependencies; CRCs and corresponding resource digests matched for 374 ZIP files; 375 NSIS files matched the expanded directory file by file |
| ZIP versus NSIS | The ZIP lacked only `resources/elevate.exe`, which is NSIS-specific; all other resources matched |
| Modules and UI | Frozen modules and static import chains for search, backup, accounts, matters, Time Machine, and other features were present; integrated search/attachment UI, data backup, and old attachment-address redirects were present; OCR resources and dependency metadata were complete |
| Frozen backend | Backend EXE digests matched between the build and final package; each of the two EXEs passed 3 positive WXGF cases and 2 explicit failure cases |
| Actual HTTP | The final package backend executed 49 synthetic-data requests, checking versions, routes, static UI, restoration/editing/repeated restoration of missing items, retention of local notes and matters, historical attachment downloads, search pagination and coverage, attachment queries, Time Machine, and access boundaries |
| Lifecycle | After the parent process exited, the watchdog shut down the backend normally; all observed child processes ended |

Final backend SHA-256: `941f2caaaf329daae7a2d6bf97769cdfb94e7f23f714743b0da3e53af52ae8bf`.

Source uses a file-by-file release allowlist. Private root tests, runtime data, account configuration, credentials, logs, screenshots, intermediate build files, and unrelated drafts are not committed. Release assets contain only the installer, portable ZIP, blockmap, and SHA-256 manifest. Private build and check records are in `.work/release-1.4.0-20261013/` and are not committed with the repository.

## Acceptance Boundaries

The frozen backend and interface used disposable synthetic data; OCR retrieval used preconstructed caches. These checks did not read real WeChat data, run real OCR/model inference, send WeChat messages, or operate the installation wizard. Actual matter system notification display, complete user materials, and different Windows devices still require separate acceptance.

These artifacts retain the existing unsigned build configuration. Package-structure and service acceptance do not substitute for operating-system signing reputation or installation-wizard acceptance.
