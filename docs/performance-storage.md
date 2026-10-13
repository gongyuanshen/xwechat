# Browsing Performance and Disk Usage

## Browsing Long Chats

When more history is loaded, unchanged messages retain their original object references to avoid updating whole batches of message bodies again. Only time separators actually displayed undergo date formatting. Calendar positioning during scrolling uses binary search to reduce per-message position reads.

In a controlled Nuxt development route, prepending 50 messages after loading 5000 text messages reduced the median load time across three runs from 780.4 ms to 149.6 ms, and the wait for two animation frames from 940.7 ms to 278.9 ms. Calendar position reads fell from 2526 to 13. The prepending reading-position deviation was 0 px; mixed-message checks covered image-group expansion and positioning to original text for hidden members.

The complete DOM of loaded messages is still retained; no virtual list or cumulative loading cap was introduced. After GC, whole-page heap memory in this controlled development page fell from 578.2 MB to 537.0 MB and still grows with the amount of history. These are not production-package or real-chat memory measurements. A native on-demand layout experiment caused reading-position shifts and was not adopted.

## Disk Usage Entry Point

Open "Settings → Disk Usage" in the left sidebar and select an account to inspect files managed by this application. Measurement starts only when this section is opened; click "Refresh Usage" to measure again.

Databases, media and attachments, search and media indexes, retained snapshots, folder copies, and other account files are counted separately. Shared AI data is displayed separately and excluded from the account total; original WeChat directories are outside the scan scope. The display reports file sizes; actual disk use and freed space may vary with the file system.

Expand "View Storage Locations" under each category or shared data to see the absolute paths of the main storage folders and click "Copy Path". Entries for the same main directory are combined; individual files, snapshot generations, and candidate subdirectories are not expanded. For example, snapshots show only `verified_snapshots`. Multiple main directories are shown separately, and categories with no saved files display an empty state. A main directory may also contain files for other accounts or categories; usage is still counted only for the selected account and corresponding category. Paths follow the currently configured output location and are blurred in privacy mode.

"Clear Search Index…" first displays an explanation, then requires the user to click "Confirm Cleanup". Currently, cleanup removes only the rebuildable chat full-text index and its temporary files; the next search rebuilds the index. Local semantic indexes, media path indexes, chat databases, anti-revoke original text, all snapshots, exports, original media, and folder copies are retained.

Cleanup is unavailable while the account is being read, synchronized, or used by a task. If automatic synchronization is using the account, click "Pause Automatic Synchronization" in this section, wait for the task to exit safely, refresh usage, and then confirm cleanup. After cleanup, the user clicks "Resume Automatic Synchronization". Ordinary status polling and late automatic-start requests do not revoke the pause. The pause lasts for the current application run; restarting the application restores default synchronization behavior.

New tasks for that account are rejected during cleanup. Directory links, overlapping sources, or corrupted metadata produce an immediate error. Partial deletion failures display the number deleted. A disconnected connection produces an unconfirmed-result message without automatic retry.

## Full-Text Indexes After Synchronization

A new snapshot's search index is prepared in a separate temporary database and published only after a complete build. When reuse requirements are met, the existing index is inherited and only changed message shards are processed. Whether a shard is unchanged is confirmed jointly by capture records and decrypted database contents. Initial creation, explicit rebuilding, or changes to shared dependencies that affect retrieval semantics still require a full build.

Indexes created by older versions lack the source records needed for reuse, so the first build after updating still requires full processing. Validating and copying an old index also has a cost; faster initial creation is not promised for every data size.

In a single retest of the final implementation with 90,000 synthetic messages, an unchanged snapshot improved from 2.131 s to 0.246 s, and a single changed shard from 2.148 s to 1.191 s. They decoded 0 and 30,000 messages respectively, with results matching the full index. Initial creation increased from 2.104 s to 2.233 s. Required tables and persistent counts in the old database are also validated before reuse, and corruption produces an explicit error; a valid empty index can be reused.

This optimization changes neither the notification policy for background automatic synchronization nor the enabled scope, model selection, or incremental rules of local semantic retrieval.

## Verification Scope

Development verification used temporary SQLite data, component tests, and controlled data on actual Nuxt routes. It did not access models, send WeChat messages, clean real user files, or perform package acceptance. Specific performance results depend on the device, message contents, and data size.
