# Dependency security remediation

The security update refreshes the Python, frontend, and desktop dependency graphs. The original 291 Dependabot alert ranges were compared against the updated lockfiles, and none matched. Dependabot scans the default branch independently and may report newly published advisories.

The optional NVIDIA GPU transcription dependencies use PyTorch 2.13 and CUDA 13.0. NVIDIA driver branch 580 or newer and Turing or newer GPUs are required.

The Python dependency audit reported no known vulnerabilities. The npm dependency audits still report the following upstream advisories, with no high or critical findings:

- [deepmerge](https://github.com/advisories/GHSA-gcqx-w48m-w2h4): the latest available release remains affected.
- [glob](https://github.com/advisories/GHSA-3g3m-2fjr-6pw4): the fixed major version is outside existing parent dependency ranges.
- [minimatch](https://github.com/advisories/GHSA-g3hv-75wg-m249): the fixed major version is outside existing parent dependency ranges, and older callers depend on removed APIs.

Upstream package updates or reviewed dependency-chain replacements are needed to address these remaining advisories. Application runtime, GPU inference, and installer acceptance are separate from lockfile and dependency audit results.
