# Development and cleanup rules

- Read the existing implementation and its callers before editing. Consult official documentation when an API or dependency is unclear; ask the user about ambiguous business rules instead of guessing.
- Reuse existing code and keep execution simple and flat. Do not add unused wrappers, compatibility layers, or speculative features.
- Do not hide errors through silent degradation, implicit retries, default success responses, or empty catch blocks. Raise clear errors at their source and display them through the existing error state.
- Configuration defaults apply only to missing configuration. Invalid settings, corrupt data, and failed reads or writes must not appear as empty data or valid defaults.
- Centralize format, permission, and provenance checks at entry boundaries. Core logic trusts validated input and does not repeat fallback checks.
- When restoring or removing a feature, inspect the complete call chain and clean up obsolete blockers, copy, adapters, state, imports, and dedicated tests.
- Before deleting code, check framework callbacks, automatic registration, dynamic imports, desktop IPC, and tool entry points. Search counts alone do not establish dead code.
- Make small changes and run relevant verification. Preserve existing user data and changes outside the task.
- Preserve permissions, privacy, account isolation, asynchronous request identity, cancellation, and consistency protections. An unknown send result is not success and must not trigger automatic resend; retain attachments and pending-review state.
- Distinguish automated tests, real model inference, real WeChat operations, and installer acceptance. Do not describe them collectively as fully verified.
- Keep GitHub presentation and repository documentation in English. Change application-interface language only when the user requests it separately.
- GitHub Release titles and notes default to English only unless the user explicitly requests another language. Notes contain changes, downloads, and necessary usage instructions; put verification results, scope, untested cases, and limits in project documentation rather than a Release verification section.
- After modifying a Release, reread its remote title and body to verify language and content.
