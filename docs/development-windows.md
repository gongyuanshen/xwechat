# Windows source development and execution

The project now uses an independent data path, and desktop packaging has been restored through a separate pipeline. The repository provides source, tests, dependency lockfiles, and one-command packaging scripts for Windows NSIS installers, portable archives, and Python wheel distributions.

## Development environment and startup

Use Windows x64, Python 3.11 or newer, uv, and an x64 Node.js distribution at version 22.12 or newer. Run from the project root:

```powershell
uv sync --locked --extra voice-transcription
npm --prefix frontend ci
npm --prefix desktop ci
npm --prefix desktop run dev
```

The startup script uses the independent data path by default and starts the Nuxt development server and Electron desktop. The backend uses the project's Python environment without downloading or preparing legacy native components. The desktop startup command enables the `voice-transcription` dependencies; the installation steps above prepare local transcription in advance.

To verify frontend static generation alone:

```powershell
npm --prefix frontend run generate
```

## Single independent data implementation

The legacy client/broker modes, component downloader, leases, and dedicated hooks have been removed from source. Components still used by independent paths, including local WXGF decoding and FFmpeg, remain, as does independent read/write compatibility for legacy archive formats. No mode switch is required.

The legacy-path cleanup performed static syntax and reference checks only. Historical acceptance records for the individual stages remain below. New releases are rebuilt from current source; source tests, frozen-backend verification, and real WeChat operations are recorded separately and cannot substitute for each other.

## Independent reading and export

After installing the source dependencies above, start from the project root:

```powershell
npm --prefix desktop run dev
```

To start only the API, run `uv run main.py`; no mode variable is required. `GET /api/health` retains `data_mode=offline` and `default_source=decrypted` so existing clients can recognize the independent snapshot source. Those values no longer represent selectable runtime modes.

Build Python distributions with `uv build`. The sdist and wheel include Python source, resources, metadata, and the local WXGF image/animation decoder `VoipEngine.dll`. Legacy client/broker components, integrity extensions, the large-image hook, and other native binaries remain excluded, as do this machine's frontend dependencies. The installed command-line entry point is `wechat-decrypt`. Package the desktop application with `npm --prefix desktop run dist`.

Provide the database key manually, or keep WeChat logged in, enter the relevant account's `db_storage` path on the decryption page, and click the key-acquisition button. The independent path uses source-based memory scanning, binds to the DLL actually loaded by the process, and verifies against that account's session and message databases. Failure raises an explicit error; it neither switches to the old hook nor starts or restarts WeChat. After scanning, exit WeChat and copy the account's complete `db_storage`, retaining `.db` files and their corresponding `-wal` files. Submit `key` and the copy's path through the existing decryption page or the JSON body of `POST /api/decrypt`. Do not put keys in command lines or acceptance reports.

The decryptor verifies every main-database page, WAL frame checksums and page authentication, source-file state before and after reading, and a complete SQLite `integrity_check`. Any failure prevents replacement of the corresponding existing valid database; pages are not zero-filled and indexes are not automatically repaired. Database shards are published individually. This is not an online transaction snapshot spanning databases, so a complete copy taken after exiting WeChat is still required.

Chat `source=auto` reads the decrypted snapshot; an explicit request for a live source errors. The chat page automatically synchronizes the current local account as described below. Corrupt shards, read failures, and missing sender mappings are not returned as normal empty chats.

The latest offline conversation previews query the message shards directly instead of relying on potentially stale session caches. Cost increases with the number of shards and conversations. Retaining the previous generation's tail after a WAL reset is permitted by the [SQLite file format](https://sqlite.org/fileformat2.html#wal_reset). Valid-frame detection checks salts first; truncated frames and checksum failures in the current generation still raise errors directly.

Exports support TXT, JSON, Excel, HTML, and directory or ZIP output. A text-only API example is:

```json
{
  "account": "account-directory-name",
  "source": "decrypted",
  "scope": "selected",
  "usernames": ["contact-username"],
  "format": "json",
  "output_mode": "folder",
  "include_media": false
}
```

Submit it to `POST /api/chat/exports` and query the existing export-task status with the returned `exportId`. Message text and metadata retain their existing serialization. Chat and Moments ZIPs use `_integrity/checksums.json` for SHA-256 content checks; they do not generate WES. HTML uses repository static assets with their provenance retained and opens through local files. Rendering, pagination, and search no longer require the old `.pyd`.

Single-file contact and generic-record exports use an adjacent `.checksums.json` content manifest; browser contact export also saves this manifest. Favorites and other prebuilt-record ZIPs reuse the same independent archive path. Upstream read failure fails the task instead of becoming a normal empty export. SHA-256 checks whether content matches the manifest; it cannot establish authorship or provenance.

`include_media=true` can use existing local resources and saved media keys; DAT retains the existing source algorithms. Missing local resources are recorded as missing. Decoding, permission, or format errors must fail the task; copying ciphertext cannot count as success. Exports can explicitly request media-key acquisition, downloading available remote media, and local voice transcription, each requiring its corresponding WeChat client, network resource, or ready model. The capability API reports the old large-image hook as unavailable. Speech-to-text uses the local model downloaded and selected in Settings. Entry points, APIs, and window automation for asking the WeChat client to transcribe have been removed. Existing WeChat transcriptions stored in the database can still be read, displayed, and reused.

The existing chat, Moments, contact, record, and account-archive export APIs support `encrypt=true` and `content_key_base64`. The latter must explicitly supply a Base64 content key of exactly 32 bytes. It is unrelated to the WeChat database key and is neither included in task responses nor persisted in configuration. ZIP/single-file outputs use WEC1; incremental directories do not support whole-package encryption. The existing `export_crypto.decrypt_export_file(..., content_key=...)` independently decrypts in the default mode and atomically publishes only after every chunk's authentication, length, and endpoint checks pass. Wrong keys, tampering, truncation, reordering, and trailing content fail directly without loading legacy client/broker/lease components. The graphical import entry still accepts ZIPs/directories; WEC files must first be decrypted with that local function. Four sets of public samples generated by real legacy components cover chunk boundaries; independent decryption and reencryption match byte for byte. See `tests/fixtures/wec1/`.

Legacy account ZIPs with `manifest.wce` / `signature.wce` independently verify signatures using the fixed original runtime's P-256 public key in all modes. If `manifest.json` / `signature.wes` are also present, all four entries are required, the canonical manifest bytes must match, and both signatures must pass. WES1/WES2 explicitly support the restored `wcdb-prod-20260907-v240-6` build; unknown builds are rejected. Following the user's choice, the build/authorization validity interval is checked only against the signed generation time, rather than rejecting an archive because it has expired on the import date. Content digests, export ID, device binding, and build binding are still checked. WES1 verifies certificates issued by a fixed root; WES2 is explicitly device self-signed and provides no external identity authentication. WES2 has passed real native vectors. WES1 has only a static specification and a controlled test root, with no real positive archive yet. Import then streams and verifies every payload's signed size and SHA-256, publishing atomically only after all checks succeed. New default ZIPs still generate only SHA-256 manifests, without legacy signatures.

WXGF decoding uses the existing `VoipEngine.dll`, without legacy client/broker/lease components. Native metadata selects static opaque JPEG, transparent PNG, or complete GIF animation. Dimensions, frame count, per-frame duration, looping, and transparency are verified; there is no mode switch after failure. Source execution starts a separate Python worker. The Windows x64 frozen backend uses `--wxgf-decode-worker input output` to start the same EXE as an independent child process, dispatching the worker before importing the API and desktop watchdog. Both paths share a 30-second timeout, export cancellation, and process cleanup. Nonzero exit, crashes, out-of-range lengths, and invalid images fail explicitly. Real onefile verification preserved the dimensions and transparency of a 32×32 JPEG, transparent PNG, and three-frame GIF, including 70/130/260 ms frame durations and loop=2. Unknown headers and truncated content failed explicitly. These controlled samples do not constitute acceptance of all WeChat media or real-client interaction.

Local Persist/PersistStore/monthly-cache emoticons derive an AES-CBC key from the current account's verified code. They bind source/account to the existing image key, decrypt the complete container, extract resources at exact database ranges, and finally check the message MD5 or the same message's extern identity. Unknown prefixes are not guessed or stripped, and reads do not automatically scan the WeChat process or access remote resources. The default offline message list and location entry use local emoji GET: 404 means genuinely missing, while 422 identifies a container/key/identity error. Explicit user downloads use the retained remote address. Historical acceptance included real samples and evidence for every animation frame.

In verification processes with Python time set to 2027/2030, the old build manifest was classified as expired, while default decryption, reading, text export, and startup still passed without entering the old time-limited authorization path. These tests did not change the Windows system clock and cannot establish that the decoder DLL contains no other expiry mechanism.

Local MP4 export checks container length and uses the project's existing FFmpeg to fully decode video and audio. Corruption, no frames, timeout, and missing FFmpeg fail explicitly. Desktop startup supplies the FFmpeg installed with dependencies. When starting the Python API alone, add FFmpeg to PATH or set `WECHAT_TOOL_FFMPEG` to its executable's absolute path. Validation is limited to 120 seconds per video; user cancellation terminates and reaps the decoding process. DAT checks the declared AES plaintext length and tail boundaries. Known media and files in merged records reuse the same export path; read or decode errors do not become normal missing-resource results.

If the desktop player fails to load or has no displayable video frames, it shows an explicit message and pauses playback. Users can click **Generate local preview** to locate the original through the existing local-video API and produce an H.264/AAC copy with FFmpeg; conversion is not automatic. The task has a total 120-second limit. Closing, cancelling, or switching chats cancels the request and waits for the conversion process to exit. MP4 input boundaries, complete output decoding, and the original file's digest before and after conversion must all pass. Successful previews are cached and verified by account, source, content digest, and encoding parameters. Original-video GET and export preserve the original bytes. Real Electron verification covered the HEVC no-picture message, clicking, cancelling, generating again, and decoding 640×368 frames.

Failure to obtain platform capabilities on the decryption page shows the original error rather than assuming Windows or enabling image scanning by default. The database-key button can detect again before continuing; the image step offers an explicit retry. Scanning is enabled only when the backend explicitly declares the capability available.

This implementation changes the data path; it is not an application-wide network firewall. AI and existing explicitly networked features remain controlled by their own configuration. The historical phase-one and phase-three plans recorded the design, results, and acceptance boundaries described here.

## Independent key acquisition and batch-verified snapshots (first phase-two iteration)

`GET /api/get_keys?key_mode=pure_memory&db_storage_path=...` performs a read-only scan of running Windows WeChat and returns `method=pure_memory`, `verified=true`, `verified_roles`, and the key on success. Existing `key_v4` and `auto` modes use the same source path in offline mode. The endpoint does not present saved keys as scan results or immediately save candidates; keys are saved through the existing decryption flow after verification. Scanning and cross-database verification passed on local WeChat **4.1.15.13**; this does not establish compatibility with other versions.

`POST /api/decrypt/snapshot` accepts the same JSON fields as the existing decryption API: `key` and `db_storage_path`. The latter must be an absolute path to one account's complete `db_storage`. When active SHM exists on Windows, read-only file handles and the SQLite locking protocol pin each source database's committed WAL prefix. The fixed bytes are copied and checked repeatedly; ordinary appends beyond the commit boundary are allowed. Other sources retain batch-manifest, file-state, and SHA-256 stable observations. All business databases in the captured copy are then decrypted and integrity-checked. The response contains `generation_path`, `account_path`, per-database diagnostics, and the actual `guarantee`. This entry accepts only encrypted source databases that pass key authentication; plaintext SQLite uses the existing import entry. Lock conflicts, changed source identity, corrupt databases, copy failure, and persistence failure never return success or trigger implicit retries. Existing account directories are not replaced.

New snapshots are placed under `verified_snapshots/generation-.../databases/<account>` in the current output directory. This standalone build endpoint creates candidates only; it does not switch the current account. The refresh service below handles verification and publication.

## Automatic synchronization and top-bar refresh

Loading the current local account on the chat page automatically starts synchronization. After each check finishes, it waits **30 seconds** before the next check. Once collection, decryption, and verification complete, the conversation list and current message window update while retaining reading position. The top-bar **Refresh messages** button triggers synchronization immediately; if a round is already running, it waits for that round to finish. The interface shows a short synchronization status, and on failure displays the original reason with **Retry synchronization**.

The source must be the complete `db_storage` explicitly saved for that account, and its key must be strictly bound to that source. Imported historical records do not start local synchronization; top-bar refresh only rereads existing records. Accounts without a bound local-data directory show the reason explicitly. The backend status API declares this capability with `refresh_available` and `unavailable_reason`; startup validation still explicitly reports corrupt keys or sources.

Account switching stops the old account's task and waits for exit before starting the selected account. Leaving the chat page stops frontend status polling while the backend continues synchronizing the current account. Exiting the application stops the backend task; opening the chat account next time starts it automatically again.

| Endpoint | Parameters and behavior |
| --- | --- |
| `GET /api/decrypt/snapshot-refresh/status?account=...` | Returns stage, enabled state, latest check/success times, publication revision, occupancy, and error |
| `POST /api/decrypt/snapshot-refresh/start` | JSON `account`; `interval_seconds` defaults to 30 |
| `POST /api/decrypt/snapshot-refresh/once` | JSON `account`; runs one round, or wakes the check for an already-enabled matching account |
| `POST /api/decrypt/snapshot-refresh/stop` | JSON `account`; candidates still being built will not publish after stopping |

Run only one backend instance per output directory: the lock coordinating publication and legacy writes is currently process-local. The process refreshes only one account at a time. The chat page switches synchronization accounts in the order stop, wait for exit, then start. A 202 response from the start endpoint means only that the task was accepted; assess results through status `revision`, `last_success_at`, and `error`. Capture changes, authentication/integrity errors, permission/disk errors, and source changes stop the task without automatic retry. Stopping during decryption waits for the actual build to finish but does not switch the current snapshot.

Publication atomically replaces only `_snapshot_current.json` in the stable account directory. Global output, keys, AI data, and user-asset directories are not migrated. Each HTTP request and background export/index task reads one fixed generation. Chat pagination responses include `snapshotGeneration`; the frontend rereads the window if the generation changes. Search indexes and annual-statistics caches are generation-isolated, so late completion of an old task cannot overwrite new-generation results. The interface retains historical reading position and refreshes the latest messages when the reader is at the bottom.

Old generations and cancelled candidates are retained without automatic deletion. Accumulated snapshot-copy capacity has no fixed limit. Before each build, its estimated footprint (twice the source-file size) is compared with current free disk space; insufficient space stops explicitly. This disk check is a build preflight, rather than a filesystem hard quota. While snapshots are managed, legacy decryption, same-name import, and live-synchronization writes are explicitly rejected. Complete account ZIP export and account deletion are integrated as described below.

`per_database_committed_prefix` means every database has its own pinned commit boundary. Results include each database's page size, committed frame count, and page count, with `captured_prefix_unchanged=true`; the continuously written source directory as a whole is not claimed unchanged (`source_unchanged=null`). `stable_observation` means the source manifest, state, and content were observed unchanged during the copy window. Mixed sources return `mixed_per_database_capture`. Every mode performs page/WAL authentication and SQLite integrity checks; none represents a global transaction spanning databases or immediate new-message notification. Before capture on Windows, the DMS lock determines active SHM ownership: active connections use a pinned committed WAL prefix. For SHM left by closed connections, an exclusive DMS lock prevents reinitialization, and stable observations verify the main database and complete WAL instead of using stale SHM headers to define the commit boundary. Online capture failure does not fall back to another mode.

The latest local WeChat 4.1.15.13 acceptance included 600.05 seconds observing real sends, 19 scheduled checks, and 14 published generations. Each generation pinned 24 source databases and verified 21 business databases; 6 generations contained actual committed WAL. There were 47 new messages (17 text, 8 images, 19 emoticons, and 3 file-type messages), spanning 516 seconds with a rolling 30-second peak of 10 messages. All 21 rounds of live old/new reader, SQL, API, and JSON ZIP comparisons agreed with rechecks of the 14 generations. Old text remained fixed at 183 messages; new text reached 200. The final source check occurred approximately 18 seconds before the observation window ended; no capture outside that window was appended. This does not promise coverage for arbitrary write rates or the contents of every special-message attachment. The observer exited, WeChat-source writes were zero, and global keys remained unchanged. The preceding failed 363-second report was retained along with the subsequent fix and evidence.

### Independent account ZIPs and deletion

Offline mode uses the existing **Export account archive** entry (`POST /api/account/archive_export`). Databases are fixed to the snapshot generation at task creation, while resources come from the stable account directory. Old root databases and their logs, source paths, keys, snapshot pointers, and derived caches are not packaged. Active WAL/journal files cause an explicit failure; incomplete copies cannot count as backups.

The new ZIP's `_integrity/checksums.json` uses `format=xwechat-sha256` and `version=1`, recording every file's path, length, and SHA-256. Digests come from actual ZIP bytes. It provides content-integrity checking, rather than a signature or provenance authentication. Import retains the existing ZIP entry and strictly checks the manifest, file set, paths, lengths, digests, and SQLite `integrity_check`. Installation occurs only after all checks pass; a same-name original account is backed up, and failure rolls back. Databases are copied byte for byte without modifying the import source.

Explicit account deletion first blocks new work, stops the account's refresh, and waits for it. If read requests, queued exports/indexing/transcription, reads offloaded to threads, internal file downloads, or AI tasks have not actually exited, it returns 409 and retains the data. Once idle, it preflights and deletes the owned stable directory, all published/unpublished generations, internal export cache, avatar cache, import backups, and keys. Original WeChat sources are outside the deletion target; archives saved to other user-selected directories are also retained. Unknown ownership, links, and out-of-bounds paths are explicitly rejected. Execution-time failure reports the stage and items already cleaned, allowing explicit retry. Retries after Windows file-lock failures use directory identities persisted beforehand and cannot delete unknown directories. If account deletion succeeded but list refresh failed, completion of deletion is reported explicitly. Coordination remains limited to one backend process per output directory.

Real archive round-trip verification found identical digests and successful SQLite integrity checks for 21 databases before and after import, with no differences in 160 conversations or a sample of 100 text messages. Legacy-native/external-network call probes and WeChat-source write probes were all 0. Acceptance wrote only isolated output and did not delete a real account.

## Source commit boundaries

Commit source, tests, dependency lockfiles, development tools, necessary resources, and public certificates. Retain existing third-party notices.

The following remain local and are excluded by `.gitignore`:

- `.env`, personal configuration, chat databases, keys, and `private/` signing material.
- `output/`, `data/`, `local_search_models/`, logs, caches, and temporary verification files.
- `.venv/`, `node_modules/`, generated frontend/backend resources, and `desktop/dist/` installer output.
- Local acceptance reports and tool-observation records.

`.gitignore` does not automatically remove files already tracked by Git; inspect the staging area before committing. Source changes do not automatically modify installed applications or existing Release attachments.
