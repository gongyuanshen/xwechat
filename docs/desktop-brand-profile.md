# Desktop Branding and Persistent Data Directories

The desktop application, npm package, and preload branding marker use `xwechat` consistently. `window.wechatDesktop` remains the existing WeChat desktop IPC interface name.

Electron uses the package name to determine `userData` by default, and `sessionData` follows `userData` by default. To prevent accounts, settings, and browser storage from appearing reset after renaming, the application explicitly fixes both directories before acquiring the single-instance lock and before `ready`:

- Development builds always use `appData/wechat-data-analysis-desktop`, preserving the original development environment's data isolation.
- Installed builds prefer `appData/xwechat` when it already contains persistent state.
- Installed builds retain the old directory when only that directory contains persistent state.
- Fresh installed builds use `appData/xwechat`.

Persistent state is determined from directory entries at existing storage entry points. The presence of either configuration file, `desktop-settings.json` or `ai-notifications.json`, counts as existing state. The `Local Storage` or `output` directory must contain entries; empty directories do not count as existing state. Account databases and configuration contents are not read. Corrupted configuration continues through the existing configuration parser, which exposes errors. Missing directories and read failures are handled separately; permission and other read errors are thrown directly.

This flow does not migrate, rename, or delete any existing directory. Existing explicit configuration rules for `WECHAT_TOOL_DATA_DIR` and `WECHAT_TOOL_OUTPUT_DIR` still apply. The old package name is retained only as a necessary historical storage path and regression-test data.

All native child windows and their descendants use the current project icon. Native titles for public-account articles remain empty; other windows initially use `xwechat`, and web pages can still update their titles normally.

References: [Electron app path and name APIs](https://www.electronjs.org/docs/latest/api/app#appsetpathname-path), [Native child-window creation rules](https://www.electronjs.org/docs/latest/api/window-open).
