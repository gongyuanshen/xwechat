# Windows Path Encoding Conventions

Paths containing Chinese characters must retain their original strings after initial selection, configuration saving, upgrade backfilling, and data migration.

- Electron configuration and `output-location.path` use UTF-8; PowerShell reads must explicitly specify `-Encoding UTF8`.
- Windows PowerShell 5.1 script sources containing Chinese characters retain a UTF-8 BOM. Configuration files use UTF-8 without a BOM; these serve different purposes.
- Do not fix paths by guessing encodings, ignoring decoding errors, or changing the user's system code page.

## Verification

On Windows, run `npm ci --ignore-scripts` with `desktop` as the working directory, then run:

```powershell
node --test tests/desktop-settings.test.cjs tests/output-dir.test.cjs
```

Source tests cover saving and rereading configuration paths containing Chinese characters, spaces, and special symbols. Data-directory migration and restoration of the default directory should also verify the original path strings and file contents.

## Historical Installer Issue

Further installer and portable-archive releases have been paused, and the NSIS installer, dedicated tests, and workflows have been removed. The following record preserves the context of a known issue; it does not mean an installer is currently provided.

Versions 2.3 and 2.4 had the same encoding defect: a Chinese directory could be saved correctly on first selection, but the installer produced garbled text when reading it again. Regression verification must therefore cover rereading rather than checking only that initial selection succeeds.
