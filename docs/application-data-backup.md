# Application Data Backup and Restore

In **Settings → Data Backup**, select a WeChat account and click Export to download a ZIP of that account's application data. Saved materials usually contain original chat text and attachments, so store the file somewhere you trust. The backup is an ordinary ZIP; SHA-256 checks integrity and does not provide encryption or source authentication.

## Backup Contents

| Content | What is retained |
| --- | --- |
| Matters | Titles, status, deadlines, sources, change history, reminder revisions, and associated undelivered reminders |
| Folders and topic archives | Folder names, all saved versions, materials within each version, schedule changes, and conclusions requiring correction |
| Current materials | Copies of original message text, notes, tags, manual verification markers, and saved copies of attachment extraction/OCR text |
| Saved reports | The current edited body, original answer, citation evidence captured when saving, and existing metadata such as the analysis scope |
| Saved searches | Names and complete filters, including fixed or rolling date settings |
| Attachments | Saved files referenced by current materials and all historical topic versions; each copy is packaged only once |

Unsaved drafts in editing fields are not included. This ZIP excludes WeChat chat databases, original attachment source directories, unsaved Agent conversations and runs, model configuration and keys, and rebuildable retrieval/OCR caches. Continue using the existing "Export Account Archive" for chat data. Original text, report evidence, and OCR text copies already in the backup can be read independently; "Open Original Message" still requires the corresponding chat database.

Export uses a single consistent record snapshot. Later changes to current materials do not alter an already generated ZIP. Relevant attachment copies are retained while files are copied; missing files, digest mismatches, or read/write errors fail explicitly rather than being skipped and reported as success.

## Restore to the Local Library

1. In "Data Backup", select the downloaded ZIP and click **"Restore Missing Materials, Keep Local Versions"**. You do not need to import the original WeChat account first.
2. The application validates the format, record relationships, file set, sizes, and SHA-256 in a temporary directory, then writes missing records and attachments to the active `ai/ai.sqlite3` and `ai/library/`. The temporary directory is an internal validation step; no manual file moves are required.
3. After restoration, the application shows actual added and retained counts and reasons for excluded items. Click **"View Materials"** or **"View Matters"** to use them. You can also switch accounts using "Application Data Account" on either page.
4. Open materials, matters, saved searches, and topic archives for the same account reload their data. In the folder view, you can inspect, rename, or delete restored search criteria. Rerunning searches, opening original messages, and updating matter deadlines from original chat text still require importing the corresponding chat database.

**Restoration always adds missing items and keeps local versions on conflict.** Existing records are not overwritten by the backup, and materials added locally are not deleted. When topic version numbers conflict, the local version is retained; subsequent history that depends on the conflicting version is explicitly listed as excluded. Other accounts, model configuration, and unsaved Agent runs are unaffected. A missing attachment file can be restored when its digest in the local record matches the backup. If an existing file's digest conflicts, this restoration is explicitly rejected without overwriting the local file.

Only undelivered reminders for matters newly restored in this operation are imported from the backup. Existing matters and reminder states retain their local versions; repeated restoration does not queue these old reminders again. Newly restored matters participate in the existing scheduler according to their original reminder settings, so overdue reminders may appear. Queuing a notification does not mean the user has seen the system notification.

Validation or write failures do not commit partial new materials. If writing completes but cleanup of staged files fails, the application explicitly shows "Materials Restored" together with the cleanup error. A connection interruption shows that the result is unconfirmed and does not trigger an automatic retry; inspect local materials first. Changes to local materials during restoration explicitly return a conflict, requiring the user to start again.

The archive's "Notes" and "Reports" counts cover current materials in the backup; historical version contents are not counted again in those two totals. "Attachments" counts the combined set of files referenced by current and historical materials. Added/retained counts reflect the actual commit in this operation; the backup total must not be presented as the added count. Integrity validation does not establish that report conclusions are correct, OCR has been manually verified, or sources are authenticated.

## Verification Record

Restoration into the active library passed 167 focused backend regressions, 132 focused frontend regressions, a production build, and a browser workflow over real HTTP. In a controlled environment, restoration from an empty library, subsequent note editing, historical attachment reads, repeated restoration, and matter updates all passed. Repeated restoration added 0 items in every category, and local changes and existing model configuration were retained. No real user materials were modified, no package was built, and large-attachment or cross-machine acceptance was not performed.

Earlier tests covered only isolated restoration and do not serve as acceptance evidence for restoration into the active library. After updating the source, an already running older backend must be restarted to load the new endpoints.
