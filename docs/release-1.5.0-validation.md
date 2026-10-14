# v1.5.0 Release Validation Record

This record contains development and package acceptance evidence. GitHub Release notes contain changes, downloads, and necessary usage instructions.

## Changes Since v1.4.0

- Select a topic and saved version directly in Agent, or open Agent from a saved topic version.
- Bind each conversation to its captured version, show actual reading coverage, and open preserved material text from citations.
- Keep provenance distinct for original messages, extracted or OCR text, manual notes, saved reports, report evidence, and attachment metadata.
- Support saved-topic conversations for restored application-data accounts without their original chat databases.
- Preserve selected accounts during initial chat-account loading; clear outdated topic selections when changing sources or conversations.
- Retain topic scope, coverage, original citation evidence, and extraction metadata when saving reports to the library.
- Enforce account and saved-material access boundaries for historical execution and internal file tools.
- Standardize the GitHub homepage, guides, and issue templates in English.

The package also includes the existing unified search and attachment view, application-data backup and missing-item restoration, matters, topic history, and Time Machine features from v1.4.0.

## 2026-10-14 Verification

| Layer | Results and scope |
|---|---|
| Related backend regressions | 399 checks passed, including 32 saved-topic integration cases and 29 saved-material cases; two existing LangChain streaming beta warnings |
| Full frontend regressions | 85 Node tests and 1,268 Vitest tests passed across 102 Vitest files |
| Full desktop regressions | 144 checks passed |
| Dependencies and builds | `uv lock --check`, icon generation, 43-route static UI generation, PyInstaller backend, NSIS installer, and portable ZIP builds passed |
| Package inspection | Application and numeric PE versions matched `1.5.0` and `1.5.0.0`; ASAR contents, archive CRCs, NSIS payloads, and corresponding unpacked resources passed inspection |
| Feature inclusion | Saved-topic modules and the compiled picker, coverage notice, material reader, and privacy classes were present; existing search, backup, OCR resources, and dependency metadata passed inspection |
| Frozen backend CLI | Both freshly built and packaged backend executables passed three positive WXGF cases and two explicit failure cases each; backend digests matched |
| Actual packaged HTTP | 58 synthetic-data requests passed, including saved-topic creation/history/version binding, restored-account access, cross-account and invalid-scope rejection, backup restoration and repeated restoration, historical attachment downloads, search coverage/pagination, matters, and Time Machine |
| Lifecycle | The parent watchdog shut down the packaged backend normally; observed descendants exited |

The source release uses an explicit 38-file allowlist. Private root tests, runtime data, account configuration, credentials, logs, intermediate build files, and five unrelated drafts remain outside the commit. Release assets contain the installer, portable ZIP, installer blockmap, and SHA-256 manifest. Private evidence is retained in `.work/release-1.5.0-20261014/`.

## Acceptance Boundaries

Backend graph regressions used controlled model responses and isolated synthetic materials. Packaged HTTP checks used synthetic records and precomputed extraction caches. These checks did not call real model or OCR services, read real WeChat data, send messages, exercise the installation wizard, or perform interactive visual acceptance.

Saved-topic questions use only the selected version's preserved text. Raw attachment copies contribute metadata unless extracted or OCR text was saved in that version. Complete saved-text reading does not establish complete original-chat coverage or correctness of model conclusions. See [Saved-topic questions](chat-agent.md#questions-about-a-saved-topic-version-2026-10-13).

The artifacts retain the existing unsigned build configuration. Archive and backend checks do not substitute for installation-wizard acceptance, operating-system signing reputation, or acceptance on other Windows devices.
