# Local Folders, Saved Searches, and Attachments

These features organize frequently used queries, original chat text, attachments, and Agent reports within the current WeChat account. Materials stay on the local computer, and editing a material copy does not change the original WeChat record. The sidebar's "Local Folders" and "Search" entries belong to the application and are separate from the "Favorites" page that reads existing WeChat favorites.

## Save and Reuse Search Criteria

1. Open message search on the chat page and set keywords, message types, and other filters.
2. Expand **"Saved Searches"** in the search sidebar, enter a name, and click **"Save Current"**.
3. Click a saved name to restore its criteria and search current data again from the first page. You can rename or delete it using the adjacent controls.

Saved combinations currently include keywords, keyword/smart search mode, message type, current-conversation/all-chats scope, conversation, sender, group/private chat filters, and time range. The default message type is text; you can also select all types, files, images, videos, and others. In keyword mode, after selecting a specific message type, you can leave the keyword empty to browse that type directly. Selecting all types or using smart search still requires keywords. An empty keyword in smart search produces an explicit error; you must select keyword mode to browse by type alone. Smart retrieval retains the existing search behavior for index requirements and coverage notices.

**Dates can be saved as a fixed range or a rolling last N days.** Actual start and end times are recorded by default; older saved searches continue using fixed ranges. Select a time range such as "Last 7 Days", then check "Search the Last N Days Each Time" in the save area. Dates are recalculated only when applying that saved search; subsequent pages in the same query keep a consistent time range. After applying a fixed range, the date controls show the corresponding dates while the query retains the precise times recorded when saving. Manually changing the dates searches again using the selected dates. Old results, pagination positions, and retrieval tickets are not saved. An error is shown if the conversation is no longer in the current account.

## Add Chat Messages to a Folder

1. Open the context menu on an original chat message and select **"Save Original Text to Folder"**. Files, images, and videos also offer **"Save Attachment Copy to Folder"**.
2. Select an existing folder or create one in the dialog. You can enter a title, notes, and comma-separated tags.
3. Click **"Save Copy"** or **"Save Attachment Copy"**, then view the result in **"Local Folders"** in the sidebar.

When saving, the application checks the original message against the current account, conversation, and message location identifiers, then saves the original text and source information such as sender, time, and type. Entries without verifiable original-message locations, such as subrecords in merged forwards or synthetic records in floating windows, do not show this entry point. The original message must be readable on the first save; source verification failures produce an explicit error.

"Save Original Text to Folder" saves original text and source information. For files, images, and videos, you can also save an attachment copy. "Add to Folder" in the attachment view directly saves the attachment file. Success appears only after the copy has been written successfully. An unreadable local file, mismatched source, or insufficient disk space produces an explicit error.

When a file, image, or video is not found locally, saving and downloading prompt you to open the corresponding message in desktop WeChat, download the original attachment/original image/full video, and then return to retry. If it is still not found, refresh the current account's data and try again. A failed save keeps the title, notes, tags, and selected folder in the dialog and does not show success. An image that has been found but cannot be decoded or decrypted produces a different error rather than being incorrectly reported as not downloaded.

If a file message lacks attachment location information, or the account lacks an accessible attachment source directory, the specific reason is reported explicitly. These cases require inspecting message data or resource sources; a download prompt does not replace those errors.

Attachment materials retain the filename, size, SHA-256 digest, and original message source, and can be downloaded independently from Local Folders. Images and videos offer previews. If the original file is later lost, the saved attachment copy remains downloadable; "Open Original Message" depends on the original record still existing. Older original-chat-text materials are not automatically converted into attachment copies. Voice and other message types can still save original text, but do not offer attachment file saving.

**The saved image is the version currently decodable on the local computer; it may be a thumbnail, and original-image quality is not guaranteed.** The save dialog and material details explain this limit. Saving a copy does not recover an original image from WeChat.

## Find and Process Attachments Across Chats

1. Open **"Search"** from the sidebar, switch to **"Attachments"**, and select an account to browse files, images, and videos. Keywords and dates are shared with "All Content"; an empty keyword lets you browse attachments.
2. Filter by name, type, conversation, sender, and start/end dates. Names and bodies can be searched separately or together. The existing local message index must be ready on first use, and the page shows its status.
3. Click **"Original Message"** to return to the source. Images and videos can be previewed; all supported attachments can be downloaded or added to a folder. Attachments not downloaded locally show a read failure and are not automatically fetched externally.
4. For ordinary document text, click **"Extract Text"** on one item or **"Extract Text from Files on This Page"**.
5. For images and PDFs, click **"Recognize Image Text"**/**"Recognize Text in PDF Images"** on one item, or **"Recognize Text in Images and PDFs on This Page"**. Batch operations can stop subsequent attachments and stop after the current attachment finishes. After completion, rerun the body-text search.
6. Successful extraction/recognition of one item immediately expands its full text. For existing results, click **"View Full Text"**. The expanded area offers **"Copy Full Text"**, **"Export TXT"**, and **"Save Recognized Text"**. These operations use the complete cache rather than list snippets. Copy failures are reported explicitly; you can also select and copy the full text directly.

**"Add to Folder" saves the attachment file; "Save Recognized Text" saves a text copy and its source.** Text materials retain the extraction method, OCR engine, input file digest, and extraction time, while the original message remains locatable. Later cache updates do not overwrite saved copies. Within a folder, you can add notes/tags, mark manual verification, and export Markdown/HTML; derived text is read-only. Only complete, nonempty results can be saved as text materials; historical partial results identify their scope. If the result or source changes before saving, you must review it again; a different version is not silently saved.

Ordinary text extraction supports TXT, MD, CSV, PDF, DOCX, XLSX, and PPTX, and parses only existing text. Local OCR supports image messages, static PNG/JPEG/WebP/BMP/TIFF files, scanned PDF pages, and embedded images; existing PDF text is also retained. Results mark page numbers/image positions and "Local OCR" and require checking against the original image. Image messages may provide only locally readable thumbnails, with no guarantee of original-image quality. Detecting no text does not establish that an image contains no text.

OCR uses RapidOCR 3.10.0 weights collected with the project dependencies and desktop build. It runs on the local CPU or a verified AMD DirectML GPU, without downloading models during recognition or calling cloud services. Manage devices and components in **Settings → Local Acceleration**; the attachment view also provides an entry point. Completed results record the actual device used for that run, any partial CPU execution, and the reason for switching to CPU. See [Local Acceleration](local-acceleration.md) for usage and the limits of AMD measurements. Missing/corrupted models, corrupted images, animated or multiframe images, and images exceeding pixel limits produce explicit errors.

Body-text search covers only text already extracted or recognized, and the page displays coverage for the current filters. The cache is published atomically only when an entire item finishes; failures do not mark partial results as complete. Repeating ordinary extraction on the same file retains any existing complete OCR cache. This feature requires an explicit action and does not automatically scan the whole library. Ordinary chat keyword search still searches original message fields. Literal OCR search uses body-text search in the attachment view; smart hybrid retrieval requires updating the local retrieval index before it can use these caches. Agent citations retain message sources and OCR markers; recognized text requires manual checking.

Text parsing and OCR have a per-file limit of 200 MB; the Office decompressed-content limit remains the parser's 512 MB. Exceeding a limit is explicitly rejected rather than truncated into a successful result. Attachment copying and downloading do not use this parsing limit; actual available disk space is checked before copying.

## Save, Edit, and Export Agent Reports

1. Once an Agent answer is complete and has a body, click **"Save Report"** below it, choose a folder, and save a copy. "Conversation" mode in the in-chat AI panel uses the same entry point.
2. Select the report in **"Local Folders"** in the sidebar. You can edit its title, report body, notes, tags, and folder, then click **"Save Changes"**.
3. After saving changes, select **"Export Markdown"** or **"Export HTML"** to obtain a file readable across machines.

Saving a report retains the original answer, original citation evidence, and available scope, time, coverage, and model metadata. Editing the body affects only the material copy; the original answer and citations remain. The page lets you view the original answer and sources. Exports include source information and, when the body has been edited, the original answer as well. If the report's original task or required citation evidence is missing, a successful save cannot be fabricated.

AI summaries and inferred relationships require manual checking. Materials default to **"Awaiting Manual Verification"**. After checking, select **"I Have Manually Verified This Material"** and save. The user maintains this marker; it does not mean the application automatically verified the report's conclusions. Original evidence copies are not source authentication or digital signatures.

## Continuously Updated Topic Archives

Each folder can serve as a topic, such as "Paper Submission", "A Trip", or "A Project", without creating a second library.

1. Create or select a folder and save relevant materials from different chats, the attachment view, and Agent answers. Existing materials can also be moved by changing "Folder" in their details and saving.
2. After selecting a folder, inspect the comparison between current materials and the last saved version in **"Topic Archive"** on the right. You can reopen it using **"Topic Archive · Versions and Changes"** above the material list. On narrow screens, click "Back to Material List" to return to materials.
3. The comparison explicitly lists **Added, Removed, and Modified** items. Expand an item to read the full saved body and notes. Modified items show the before/after body, title, notes, tags, and verification state side by side. Reports retain the original answer, original-text evidence, and sources; original-message buttons continue using the existing navigation flow.
4. Enter a version name and optional **"Schedule Changes"** and **"Conclusions Requiring Correction"**, then click **"Save New Version"**. These two fields are manual user records describing changes to dates/responsibilities and checks of earlier conclusions. The application does not present text differences as automatically understood plan changes and does not automatically call models.
5. After manually adding new original text, attachments, or reports, return to the topic to inspect changes. You can continue using "Saved Searches" in chat to search current data again, then add selected materials to the same folder. Topics do not execute searches or full-library scans automatically.
6. Select a historical version in **"View Version"** to inspect its schedule notes, conclusions requiring correction, and comparison with the previous version, or switch to **"All Materials in This Version"**. Historical versions are read-only snapshots; later material edits or deletion of the original Agent task do not alter them.

Versions are stored in the existing account-isolated AIStore without adding an external service. Each save copies material text, citations, and report evidence. Attachment files are immutable saved local copies referenced jointly by current materials and historical versions; files are not duplicated for every version. After deleting current materials or moving them to another folder, older versions can still read original text and download attachments. A file is cleaned up only when its last reference from both current materials and historical versions is removed. Deleting a folder removes all historical versions of that topic. Editing or deleting an individual historical version is not currently available.

Saving a version uses the material state just reviewed. If materials, the folder name, or historical versions change on another page, submission explicitly returns a conflict. Click "Refresh Change Comparison" and check again; there is no silent merge or automatic retry. A failed save retains the entered version description. A new version cannot be saved if a current attachment copy is missing; downloading a missing historical file returns an explicit error. Versions save only committed material changes, excluding drafts still in editing fields.

Automated verification covers before/after comparisons, independent historical contents, concurrent conflicts, cross-account isolation, original report evidence, historical attachment reads, moves across folders and final-reference cleanup, blocking final deletion during downloads, and frontend retention of descriptions after failure, asynchronous account switching, and privacy masking. Real imported WeChat data and package use still require separate acceptance and cannot be established by these tests.

## Management and Data Boundaries

- **Account isolation**: Folders, materials, and saved searches belong to the WeChat account used at creation; switching accounts shows only that account's contents.
- **Material management**: The material page can search titles, notes, and tags, move materials to other folders, rename folders, or delete copies. Original chat text and attachment-source bodies cannot be edited; report bodies can.
- **Cross-source retrieval**: Sidebar [Search](unified-search.md) queries chats, attachment extraction text, OCR text, and current material bodies/notes/tags together and shows actual coverage per source. Accounts with only restored materials and no chat database can also search saved contents.
- **Deletion scope**: Deleting a material copy does not delete the original WeChat message or file; materials and attachments referenced by historical versions are retained. Deleting a folder also deletes its current materials, all historical versions, and attachments no longer referenced by other materials/versions. Deleting a local account clears that account's materials, folders, topic versions, saved searches, and attachment copies.
- **Concurrency and failure**: While an attachment is downloading or being previewed, deleting its final material/historical-version reference is explicitly rejected; finish reading before deleting. If a record has been deleted but disk-copy cleanup fails, the interface explains both states rather than showing ordinary success.
- **Archive boundary**: Material records are stored in the application's AIStore and attachment copies in its library directory. The existing home-page "Export Archive" account database/resource archive does not contain these materials or saved searches; reimporting an account archive does not restore them.
- **Reading exports differ from backups**: Markdown/HTML can be read on another machine, but there is currently no entry point to import them back into folders or restore saved searches. Text exports do not embed attachment files; they include only filenames, sizes, and digests. Attachment copies must be downloaded separately.

Before saving, exporting, or sharing, check whether the actual body, names, chat sources, and notes contain information you do not want to disclose.
