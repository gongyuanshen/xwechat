# Startup and output-directory migration fix — 2026-11-14

## Source impact

The previous source launcher treated any frontend HTTP response as ready, including Nuxt's HTTP 503 loading page. Electron could navigate while the initial Nitro server was rebuilding and reach the existing 60-second navigation deadline.

Output-directory migration also copied absolute paths inside verified snapshot manifests unchanged. After a move, the snapshot registry correctly rejected the old generation/account/database output paths. This issue affects output trees containing verified snapshots; it is separate from frontend startup readiness.

## Fix

- Require a complete frontend HTTP 200 response. Keep HTTP 503 pending within the existing bounded wait; expose other HTTP errors and interrupted bodies. Stop the frontend process tree before propagating a startup-readiness failure.
- Validate and relocate only verified-snapshot output paths. Preserve captured-source paths, guarantees, integrity evidence, and every numeric token, including 64-bit inode and nanosecond timestamps.
- Verify copied files by SHA256; compare relocated manifest bytes with the expected transformed content. Preserve rollback and interrupted-migration recovery using the original logical source root and final destination root.
- Keep existing snapshot-registry validation, dependencies, timeouts, release version, settings, account keys, and sending behavior unchanged.

The fix prevents incorrect paths in subsequent migrations. It does not silently rewrite or accept manifests already damaged by an earlier migration. Existing affected metadata requires a backed-up, path-only repair that preserves the original evidence.

## Verification

An exact snapshot of the existing GitHub main branch was staged before applying the two code changes and two new regression files. Existing repository tests, including `desktop/tests/output-dir.test.cjs`, were retained unchanged. The new migration regressions are in `desktop/tests/output-dir-snapshot-migration.test.cjs`.

On Windows, using the already installed application dependencies:

```powershell
cd desktop
node --test
node --check scripts/dev.cjs
node --check src/output-dir.cjs
```

The full staged desktop suite passed: **159 tests, zero failures**. Initial dependency-resolution failures in the isolated staging directory were resolved by attaching the existing dependency directory; no dependency versions or test assertions were changed to obtain the pass.

The source launcher was also closed and reopened with `node desktop/scripts/dev.cjs`. Electron reached the chat interface on its first navigation attempt in approximately one second. The existing account and conversation loaded normally after the affected local metadata was repaired. All 21 verified database hashes remained unchanged and their read-only SQLite quick checks passed.

Fresh locked desktop dependencies and packaged application checks were subsequently completed for v1.5.2. The full frontend test suite remains red. See the release validation record for exact results and boundaries.

## Existing downloads

Updating the main branch does not modify an existing installer or portable archive. Version 1.5.2 provides newly built Windows installer and ZIP downloads; earlier release assets remain unchanged.

The old migration defect was reproduced using the actual module extracted from the checksum-verified v1.5.0 installer and from the actual v1.5.1 ZIP. The latter was inspected using ranged downloads and ZIP-member CRC verification, not a complete independent ZIP hash check. The v1.5.1 installer was not independently unpacked, so applying that ZIP finding to its same-version installer is an inference. Packaged applications do not execute the Nuxt development launcher.

The v1.5.2 packaged module matches the fixed source. A real packaged app loaded an isolated replica, completed the existing desktop migration API, and reopened using the new path. Its 21 database hashes and all non-path manifest values/numeric tokens stayed unchanged. The installer and portable ZIP payloads were compared with that tested unpacked app. See [v1.5.2 validation](release-1.5.2-validation.md) for coverage and limitations.
