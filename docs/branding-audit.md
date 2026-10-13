# Branding audit and consolidation (2026-10-07)

The product name is `xwechat`, and its icon source is `frontend/public/logo.png`. Earlier work updated only some screens and build product names, missing the desktop npm package name, child-window defaults, export credits, and other entry points. As a result, service-account windows still displayed an old name and Electron's default icon.

This consolidation covered desktop application and package metadata, main-window and child-window icons, page titles, canvas/SVG branding in annual recaps, export credits and project links, backend API/logs, MCP display names, and website demos. Native service-account article titles were cleared; article text and the web page's own title retained their original content. Imported-account avatar placeholders use the current icon, and the unused historical `frontend/public/Contact.png` was removed.

`desktop/scripts/build-icon.cjs` derives desktop PNG/ICO assets, the browser favicon, and the website logo from the single source image. Regenerated static UI is copied to `desktop/resources/ui`. Text replacement does not update previously installed applications or compiled binaries; this branding task did not publish an installer.

The following historical names have functional purposes and were retained with separate checks:

- `wechat-data-analysis-desktop`: a historical persistent-data directory, associated regression tests, and development acceptance tools. See [Desktop branding and persistent data directories](desktop-brand-profile.md) for selection rules. Accounts and settings are not moved, renamed, or deleted.
- `wechat_data_analysis` / `wechat_data_analysis_archive`: recognized import-format identifiers, rather than product display names.
- `wechat_decrypt_tool`, `wechatDesktop`, and `wechat.*`: Python modules, desktop IPC, and MCP interfaces whose calling contracts are preserved.
- Historical project names in README acknowledgments, website credits, `html_export/sources.json`, and CSS/JS headers: upstream attribution and resource provenance, rather than current product branding.
- Historical website screenshots: original assets were preserved, with the old application sidebar cropped at presentation time. Chat-sender avatars and WeChat's own icons continue to represent the original data.

Verification distinguishes source tests, static builds, isolated-window/website checks, and real-account acceptance. This work did not establish installer acceptance or live network acceptance of service-account articles.
