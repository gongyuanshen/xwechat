# RTX 5060 faster-whisper CUDA acceptance guide

Status: implementation complete; RTX 5060 hardware acceptance pending. Updated: 2026-07-29.

## Scope

This project's speech-to-text feature uses `faster-whisper` and CTranslate2, rather than PyTorch. The `cu128` wheel installation procedure used by comparable PyTorch voice-cloning projects therefore cannot be copied directly; this guide adopts only their RTX 50-series driver, disk, model-cache, and acceptance principles.

The default device is CPU `int8`. On a machine with an NVIDIA GPU, select NVIDIA GPU in Settings; the first transcription loads the model with CUDA `float16`. If CUDA detection or model initialization fails, the existing implementation automatically switches to CPU `int8` without disrupting voice playback, chat viewing, or export tasks.

## Server prerequisites

Start with read-only checks on the target machine:

```bash
nvidia-smi
python3 --version
ffmpeg -version
df -h
```

`nvidia-smi` must list the RTX 5060. If it is unavailable, ask the server administrator to repair the NVIDIA driver first; repeatedly reinstalling Python packages is not a way to diagnose driver problems.

This project does not require `torch`, `torchaudio`, or the voice-cloning project's wheelhouse. Install this project's current voice dependencies:

```bash
uv sync --no-editable --extra voice-transcription
```

The currently pinned `CTranslate2 4.8.1` requires the NVIDIA driver, CUDA 12.x, and cuDNN 9 runtime libraries in a Linux GPU environment. PyTorch's `cu128` wheel does not supply these libraries to CTranslate2. Complete the read-only checks first, then have the server administrator or an authorized maintainer install the missing runtimes according to the official installation instructions for the matching CTranslate2 version. If using user-level CUDA/cuDNN packages from PyPI, install them only into this project's virtual environment and configure their library paths in the same user process that starts the backend. Do not modify system `/usr`, global `LD_LIBRARY_PATH`, or a shared CUDA installation.

## Model preparation

The current default model is `Systran/faster-whisper-medium`, approximately 1.43 GiB on disk. Before production acceptance, place it in the target machine's Hugging Face cache or point `WECHAT_TOOL_WHISPER_MODEL` to a complete local model directory. Network downloads during the first transcription are disabled by default.

A data disk is a suitable cache location, for example:

```bash
export HF_HOME=/mnt/sdb/xwechat/hf-cache
export WECHAT_TOOL_WHISPER_MODEL=medium
export WECHAT_TOOL_WHISPER_ALLOW_DOWNLOAD=0
```

If the cache is not ready, temporarily set `WECHAT_TOOL_WHISPER_ALLOW_DOWNLOAD=1` during a maintenance window with network authorization, then restore it to `0` after downloading. Models and cache directories are local runtime assets and must not be committed to Git.

## CUDA acceptance

After starting the backend, first check whether CTranslate2 can detect CUDA:

```bash
python - <<'PY'
import ctranslate2
print("cuda device count:", ctranslate2.get_cuda_device_count())
PY
```

The expected device count is greater than zero. Then open **Settings → Speech to text** in the application:

1. The page should show the detected NVIDIA GPU; `nvidia-smi` supplies the RTX 5060 name.
2. Select **NVIDIA GPU**. The preference is saved in the application's `output/runtime_settings.json`.
3. Click **Transcribe** on a clear Chinese WeChat voice message, or enable **Speech to text** when exporting the same conversation.
4. Reopen Settings and confirm that **Actual device** shows **NVIDIA GPU**.
5. The export must still include playable `media/voices/*.mp3` files and Chinese transcription text.

If CUDA detection passes but model initialization fails, Settings retains **NVIDIA GPU** as the selected device and shows the automatic fallback reason; **Actual device** should show CPU. Transcription should still succeed. Record the CTranslate2, NVIDIA driver, and CUDA runtime versions before investigating.

## Configuration priority

Inference-device priority is:

```text
WECHAT_TOOL_WHISPER_DEVICE environment variable
> device preference saved in Settings
> default CPU
```

If a deployment script sets `WECHAT_TOOL_WHISPER_DEVICE`, the interface labels the device **Fixed by startup environment variable**, so users can see that changing the preference has not taken effect. When this variable is absent, select CPU or NVIDIA GPU in Settings.

## Acceptance record

After testing on the RTX 5060 server, record:

```text
GPU name, Driver Version, and CUDA Version from nvidia-smi
Python / faster-whisper / CTranslate2 versions
CTranslate2 CUDA device count
Settings detection result, selected device, actual device, and whether fallback occurred
First-transcription duration and second-transcription duration with a warm cache
Voice count and success/failure/cache statistics in the export
```

Do not manufacture a fallback scenario on a shared server by uninstalling drivers, changing system CUDA, rebooting the machine, or stopping other people's processes. Local unit tests cover the automatic fallback path; server acceptance checks the real NVIDIA GPU success path only.
