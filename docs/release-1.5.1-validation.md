# xwechat 1.5.1 validation

This security maintenance release updates the Python, frontend, and Electron dependency graphs. See [the dependency security record](security-dependency-remediation-20261103.md) for the original alert comparison and remaining upstream npm advisories.

## Build and targeted checks

- `uv lock --check` and npm lock dependency-tree checks passed. The original 291 Dependabot alert ranges no longer match the updated locks; GitHub may add further advisories.
- Static Nuxt generation completed. The packaged backend passed its isolated `--smoke-backend` check.
- The Windows backend was built with the official CPython 3.13.17 embedded distribution. The `pilk` 0.2.4 CPython 3.13 wheel was compiled from the official PyPI source archive (SHA-256 `d4a1bcf93dc6ef5e95e0cfd728ed4ef4d49f9c0476d70816fecbe456cc762e7f`) in a Windows GitHub Actions runner. Its SHA-256 is `6928f31bd9052531aefa08f7d9154a23ebf7cd5f08376fa7b6192bd65ed9e3b5`. A synthetic SILK encode/decode and SQLite vector-extension load passed before packaging.
- ZIP entries passed CRC verification. NSIS payload, ZIP, and unpacked distribution share identical hashes for their 381 common files. The installer and unpacked distribution additionally contain the same `resources/elevate.exe`. Installer, desktop executable, and backend report version 1.5.1.
- The staged source changes, static UI, packaged native files, backend archive entries and Python modules were checked for sensitive filenames, common credential patterns, private network URLs, and local user paths. These pattern checks do not establish the absence of every possible private value.

## Scope and limitations

The application installer wizard, interactive desktop UI, real WeChat accounts/data, real model inference, and NVIDIA GPU runtime were not tested. The optional GPU transcription dependencies use CUDA 13 and require NVIDIA driver branch 580 or newer and a Turing-or-newer GPU. The default Windows package does not bundle PyTorch or Transformers. No code signing was performed.

The npm audit still reports low or moderate upstream issues in deepmerge, glob, and minimatch. Dependency and packaged-backend checks do not establish complete end-user acceptance.
