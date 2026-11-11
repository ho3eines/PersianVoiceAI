# Persian Voice AI

**An offline-first Persian voice and text assistant for PC, with a LAN WebSocket transport designed for ESP32-S3 audio terminals.**

No audio, transcript, or prompt is sent to any cloud API. Speech recognition (ASR), language model inference (LLM), text-to-speech (TTS), acoustic echo cancellation (AEC), and conversation persistence all run locally on your machine.

> **مستندات یکپارچهٔ فارسی:** برای راهنمای کامل فارسی (راه‌اندازی، معماری، تنظیمات و وضعیت فعلی)،
> **[راهنمای یکپارچهٔ کامل پروژه (`docs/PROJECT_GUIDE.md`)](docs/PROJECT_GUIDE.md)** را بخوانید.
> پس از ویرایش هر سند مرجع، دستور `python tools/build_docs.py` را برای به‌روزرسانی و `python tools/build_docs.py --check` را برای بررسی هم‌خوانی اجرا کنید.

---

## Table of Contents

- [Overview & Architecture](#overview--architecture)
- [Prerequisites](#prerequisites)
- [Step-by-Step Installation](#step-by-step-installation)
- [Model Weights & Downloads](#model-weights--downloads)
- [Configuration (`.env`)](#configuration-env)
- [Quick Start: Running Inference](#quick-start-running-inference)
  - [1. Start the Local Services](#1-start-the-local-services)
  - [2. CLI Inference](#2-cli-inference)
  - [3. Python API Inference](#3-python-api-inference)
- [REST & WebSocket API](#rest--websocket-api)
- [Directory Structure](#directory-structure)
- [Testing, Benchmarks & Dataset Validation](#testing-benchmarks--dataset-validation)
- [Honest Status & Known Limitations](#honest-status--known-limitations)
- [Documentation Index](#documentation-index)
- [License](#license)

---

## Overview & Architecture

The active PC voice runtime uses an asynchronous, actor-based duplex streaming pipeline (`server/streaming_voice.py`):

```text
Continuous Microphone (20 ms PCM16 @ 16 kHz)
   │
   ▼
SpeexDSP AEC (duplex speaker reference) ──► EnergyVAD (hysteresis + pre-roll + adaptive noise floor)
   │
   ├──► Overlapping Partial ASR (3 s window, replaceable) ──► faster-whisper (local CTranslate2)
   └──► Lossless Final ASR FIFO Queue (never dropped on barge-in)
           │
           ▼
   AssistantTurn via llama-server (async SSE) ──► Sentence Chunker & Persian TTS Prep
           │
           ▼
   Local Piper TTS (male / female ONNX) ──► 20 ms Duplex Playback (cancellable barge-in)
```

Key architectural properties:

- **Hands-free live mode by default**: Speaking interrupts an in-flight assistant response (*barge-in*) without discarding or invalidating previously queued final ASR utterances (`UserUtterance`, `AssistantTurn`, `PartialASR`, and `FinalASR` lifecycles are strictly decoupled).
- **Incremental Whisper adapter**: Partial transcripts run over bounded overlapping windows (`PVAI_ASR_PARTIAL_WINDOW_S`, default `3.0` s) while final decodes receive complete utterances. This is an incremental adapter over `faster-whisper`, not a natively streaming ASR model.
- **Standalone `llama-server` backend**: FastAPI communicates with [`llama.cpp`](https://github.com/ggml-org/llama.cpp)'s `llama-server` over async local HTTP/SSE (`llama-cpp-python` is not used).
- **Explicit error handling**: Missing native AEC libraries, missing model weights, or invalid `.env` values produce clear errors and surface in `GET /ready` rather than silently bypassing safety checks.

---

## Prerequisites

| Requirement | Details & Notes |
|---|---|
| **Operating System** | Windows 10/11 (64-bit), Linux (x86_64/arm64), or macOS. See [`docs/PLATFORMS.md`](docs/PLATFORMS.md) for platform parity and verification notes. |
| **Python** | **Python 3.12 (64-bit)** is the target runtime (expected by the Windows launchers `py -3.12` and asserted by `tools/environment_test.py`). *(The model-free server and test suite have also been exercised on Python 3.11 in Linux sandbox runs.)* |
| **LLM Runtime (`llama-server`)** | Prebuilt or compiled `llama-server` binary from [`ggml-org/llama.cpp`](https://github.com/ggml-org/llama.cpp) (not installed via `pip`). |
| **SpeexDSP (Native AEC)** | **Required when `PVAI_AEC_ENABLED=true` (default).** Install `libspeexdsp` via your OS package manager on Linux or build/provide a 64-bit `libspeexdsp.dll` on Windows — **not installed by `pip`**. To run without SpeexDSP (headphones only), explicitly set `PVAI_AEC_ENABLED=false`. See [`docs/AEC.md`](docs/AEC.md). |
| **PortAudio** | Required by `sounddevice` for live microphone/speaker I/O. Bundled in Windows/macOS wheels; on Debian/Ubuntu install `libportaudio2`. |
| **CUDA / cuDNN (Optional)** | **CPU is the default** (`PVAI_ASR_DEVICE=cpu`, `PVAI_ASR_COMPUTE_TYPE=int8`, `PVAI_N_GPU_LAYERS=0`).<br>• **Optional GPU ASR (`PVAI_ASR_DEVICE=cuda` or `auto`)**: Requires an NVIDIA GPU, NVIDIA driver, and **CUDA 12.x + cuDNN 9.x** runtime libraries compatible with `ctranslate2` / `faster-whisper>=1.1`. If CUDA/cuDNN is missing or GPU loading fails, `server/voice.py` logs a warning and falls back to `cpu`/`int8`. Apple MPS is unsupported by CTranslate2 and falls back to CPU.<br>• **Optional GPU LLM**: Set `PVAI_N_GPU_LAYERS` > `0` in `.env` and use a `llama.cpp` build compiled with CUDA, Vulkan, or Metal support.<br>*(Note: Actual GPU/CUDA inference performance is **NOT MEASURED** in this repository's environment; see [`docs/SPEC.md`](docs/SPEC.md) for historical notes on 2 GB Maxwell GPUs.)* |
| **FFmpeg (Optional)** | **Not required** for live 16 kHz PCM16 microphone/speaker streaming or 16 kHz mono PCM16 WAV evaluation (`sounddevice` and `soundfile` handle PCM16 directly, and `faster-whisper` bundles `PyAV`).<br>**Recommended** if you pass compressed formats (`.mp3`, `.m4a`, `.mp4`, `.ogg`) to `LocalAudio.transcribe()` via `librosa`, or when normalizing custom recordings to 16 kHz mono PCM16 WAV for [`tools/prepare_dataset.py`](tools/prepare_dataset.py) and [`benchmarks/persian_eval.py`](benchmarks/persian_eval.py). |

---

## Step-by-Step Installation

The repository splits Python dependencies into three requirement files:

- `requirements.txt` — Minimal server & text-chat runtime (`fastapi`, `pydantic`, `uvicorn[standard]`, `httpx`).
- `requirements-voice.txt` — Full local voice stack (includes `requirements.txt` plus `faster-whisper`, `ctranslate2`, `piper-tts`, `sounddevice`, `soundfile`, `numpy`, `scipy`, `librosa`).
- `requirements-dev.txt` — Includes `requirements.txt` plus `pytest` and `starlette` for development/testing. *(Note: running audio-conversion tests in `tests/test_inference_safety.py` also requires the packages from `requirements-voice.txt`.)*

### 1. Clone the Repository

```bash
git clone https://github.com/ho3eines/PersianVoiceAI.git
cd PersianVoiceAI
```

### 2. Set Up a Python Virtual Environment

Choose **either** standard Python `venv` + `pip` (expected directly by the launcher scripts in `tools/`) **or** `conda`.

#### Option A: `venv` + `pip` (Recommended)

**Linux / macOS (Bash):**

```bash
python3.12 -m venv .venv   # or python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip

# Full voice + API runtime (recommended):
./.venv/bin/python -m pip install -r requirements-voice.txt

# Or text-only API + CLI runtime:
# ./.venv/bin/python -m pip install -r requirements.txt

# Optional: add test tooling (install after requirements-voice.txt to run the full suite)
./.venv/bin/python -m pip install -r requirements-dev.txt
```

**Windows (PowerShell):**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip

# Full voice + API runtime (recommended):
.\.venv\Scripts\python.exe -m pip install -r requirements-voice.txt

# Or text-only API + CLI runtime:
# .\.venv\Scripts\python.exe -m pip install -r requirements.txt

# Optional: add test tooling
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

#### Option B: Conda / Miniconda / Mamba

```bash
conda create -n pvai python=3.12 -y
conda activate pvai

# Optional on Linux/macOS: install native audio libraries via conda-forge
# conda install -c conda-forge portaudio ffmpeg libspeexdsp -y

python -m pip install --upgrade pip
python -m pip install -r requirements-voice.txt
python -m pip install -r requirements-dev.txt   # optional, for pytest
```

> **Tip for Conda / custom Python paths:** The Bash scripts in `tools/` default to `.venv/bin/python`, but support overriding the interpreter via `PVAI_PYTHON`:
> ```bash
> PVAI_PYTHON="$(which python)" ./tools/start_server.sh
> ```

### 3. Install Native Dependencies

1. **Install `llama-server` (`llama.cpp`)**:
   - Download a release binary from [`ggml-org/llama.cpp/releases`](https://github.com/ggml-org/llama.cpp/releases) or build from [`ggml-org/llama.cpp`](https://github.com/ggml-org/llama.cpp).
   - Ensure `llama-server` is on your `PATH`, or pass its path via `--exe` (Linux/macOS) / `-Exe` (Windows) when launching.

2. **Install SpeexDSP, PortAudio, and (optionally) FFmpeg**:
   - **Debian / Ubuntu:**
     ```bash
     sudo apt-get update
     sudo apt-get install -y libspeexdsp1 libportaudio2 ffmpeg
     ```
   - **macOS (Homebrew):**
     ```bash
     brew install speexdsp portaudio ffmpeg
     ```
   - **Windows (x64):**
     - Build or install a 64-bit `libspeexdsp.dll` (upstream source: [`xiph/speexdsp`](https://github.com/xiph/speexdsp)) and set `PVAI_AEC_LIBRARY` in `.env` (e.g. `PVAI_AEC_LIBRARY=C:/AudioLibs/speexdsp/libspeexdsp.dll`), **or** set `PVAI_AEC_ENABLED=false` in `.env` when using headphones. See [`docs/AEC.md`](docs/AEC.md) for exported symbol requirements and calibration instructions.

### 4. Create `.env` and Verify Environment

**Linux / macOS:**

```bash
cp .env.example .env
./.venv/bin/python tools/environment_test.py   # checks Python 3.12 inside .venv
```

**Windows (PowerShell):**

```powershell
Copy-Item .env.example .env
.\.venv\Scripts\python.exe tools\environment_test.py
```

*(If `.env` already exists, edit it directly rather than overwriting it.)*

---

## Model Weights & Downloads

**No model weights are included in this repository** (`models/` is git-ignored), and **automatic runtime downloading is blocked** (`local_files_only=True`). Download the required local weights beforehand and point `.env` to their paths:

| Component | Default Path | Required Files | Upstream Source | License |
|---|---|---|---|---|
| **LLM** | `models/Qwen3-4B-Q4_K_M.gguf` | Single `.gguf` file | [Qwen on Hugging Face](https://huggingface.co/Qwen) | Apache-2.0 |
| **ASR** | `models/whisper-small/` | Directory with `model.bin`, `config.json`, `tokenizer.json`, `vocabulary.txt` (CTranslate2 format, not raw PyTorch `.pt`) | [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper) / `Systran/faster-whisper-small` on Hugging Face | MIT |
| **TTS** | `models/piper/fa_IR.onnx` | Both `.onnx` and `.onnx.json` (plus optional `PVAI_TTS_MODEL_FEMALE` / `PVAI_TTS_MODEL_MALE`) | [`rhasspy/piper-voices`](https://huggingface.co/rhasspy/piper-voices) (e.g. `fa/fa_IR/amir/medium`) or [`MahtaFetrat/Mana-Persian-Piper`](https://huggingface.co/MahtaFetrat/Mana-Persian-Piper) | **Check each voice's `MODEL_CARD` / `LICENSE`** |

> **Important Piper Voice Licensing Note:** While the Piper engine (`piper-tts`) is `GPL-3.0-or-later`, each `.onnx` voice carries its own dataset license (some Persian voices are non-commercial only, while `rhasspy/piper-voices` `fa_IR` cards document CC0 training data and `MahtaFetrat/Mana-Persian-Piper` documents MIT). Always read [`THIRD_PARTY.md`](THIRD_PARTY.md) and the voice's own `MODEL_CARD` before redistributing.

### Resumable Multi-Connection Downloader (`tools/download_parallel.py`)

For multi-gigabyte model files (`.gguf`, `.onnx`, or Whisper `model.bin`), the repository includes a standard-library parallel HTTP range downloader with mandatory SHA-256 verification before replacing the target file:

```bash
python tools/download_parallel.py \
  --url "https://huggingface.co/<org>/<repo>/resolve/main/<model-file>" \
  --output models/Qwen3-4B-Q4_K_M.gguf \
  --sha256 "<64-char-sha256-from-publisher>"
```

- `--sha256` is **required** for every known model artefact — `.gguf`, `.onnx`, `.bin`,
  `.safetensors`, `.pt`, `.ckpt`, and *any* file fetched from `huggingface.co`/`hf.co`
  (including `config.json`/`tokenizer.json` that belong to a Whisper checkpoint). Take the
  digest from the publisher's card/manifest; there is no flag that skips the check.
- The digest is verified on the assembled file **before** it replaces the destination, so a
  corrupt pull never overwrites a copy that already works.
- Remote sources must be `https`; plain `http` is accepted only for `localhost`/`127.0.0.1`/`::1`.
- Interrupted downloads resume cleanly across retries without redownloading completed chunks.

---

## Configuration (`.env`)

All runtime behavior is controlled via `PVAI_*` environment variables (loaded from `.env` in the working directory or repository root, with shell environment variables taking precedence). Key settings in `.env.example`:

```dotenv
# LLM (llama-server)
PVAI_MODEL_PATH=models/Qwen3-4B-Q4_K_M.gguf
PVAI_LLAMA_SERVER_URL=http://127.0.0.1:8080
PVAI_N_CTX=4096
PVAI_N_THREADS=4
PVAI_N_GPU_LAYERS=0
PVAI_LLM_ENABLE_THINKING=false

# Local API
PVAI_HOST=127.0.0.1
PVAI_PORT=8000
# PVAI_API_TOKEN=change-me   # Mandatory if PVAI_HOST is non-loopback (e.g. 0.0.0.0)

# ASR (faster-whisper)
PVAI_ASR_MODEL_PATH=models/whisper-small
PVAI_ASR_DEVICE=cpu           # cpu | auto | cuda (falls back to cpu/int8 if unavailable)
PVAI_ASR_COMPUTE_TYPE=int8    # int8 on CPU; float16 on CUDA
PVAI_ASR_LANGUAGE=fa          # fa | auto

# TTS (Piper)
PVAI_TTS_VOICE=female         # female | male
PVAI_TTS_MODEL_PATH=models/piper/fa_IR.onnx
# PVAI_TTS_MODEL_FEMALE=models/piper/fa_IR_female.onnx
# PVAI_TTS_MODEL_MALE=models/piper/fa_IR_male.onnx

# AEC (SpeexDSP)
PVAI_AEC_ENABLED=true         # set to false when testing with headphones without libspeexdsp
PVAI_AEC_LIBRARY=             # Windows: absolute path to 64-bit libspeexdsp.dll; Linux: empty
PVAI_AEC_DELAY_MS=40
```

See [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) for the complete reference of every `PVAI_*` variable, valid ranges, and `.env` parsing rules.

---

## Quick Start: Running Inference

### 1. Start the Local Services

All launcher scripts in `tools/` ship with both Bash (`.sh`) and PowerShell (`.ps1`) equivalents.

**Terminal 1 — Start `llama-server`** (reads `PVAI_MODEL_PATH`, `PVAI_N_CTX`, `PVAI_N_THREADS`, and `PVAI_N_GPU_LAYERS` from `.env` via `python -m server.config --launch`):

```bash
# Linux / macOS
./tools/start_llama.sh --exe /path/to/llama-server   # omit --exe if llama-server is on PATH
```

```powershell
# Windows (PowerShell)
.\tools\start_llama.ps1 -Exe D:\Lama.cpp\bin\llama-server.exe
```

> **Note on manual `llama-server` launches:** `PVAI_N_THREADS` and `PVAI_N_GPU_LAYERS` are passed to `llama-server` by `tools/start_llama.sh` / `tools/start_llama.ps1` (code default for `PVAI_N_THREADS` is `0`, while `.env.example` sets `4`). If you launch `llama-server` manually instead of using the launcher scripts, keep your CLI flags aligned with `.env` or `llama-server` defaults will take precedence.

**Terminal 2 — Start the FastAPI Server:**

```bash
# Linux / macOS (uses tools/start_server.sh)
./tools/start_server.sh
```

```powershell
# Windows PowerShell (uses tools\start_server.ps1)
.\tools\start_server.ps1
```

*(Alternatively, run `python -m server` directly inside your activated environment.)*

Verify readiness before sending inference requests:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

Interactive OpenAPI documentation is available at `http://127.0.0.1:8000/docs`.

---

### 2. CLI Inference

**Terminal 3 — Choose Voice Mode or Text Chat Mode:**

#### Live Duplex Voice Assistant (default)

```bash
# Linux / macOS
./.venv/bin/python -m server.voice

# Windows (PowerShell)
.\.venv\Scripts\python.exe -m server.voice
```

Useful flags:

```bash
# List available microphone & speaker devices/indices
python -m server.voice --list-devices

# Legacy Enter-triggered turn-based recording mode (debug only)
python -m server.voice --turn-based
```

#### Persian Text Chat CLI

```bash
# Interactive multi-turn Persian chat (/help, /clear, /stats, /exit)
./tools/chat.sh                        # or: .\tools\chat.ps1
python -m server.cli

# Single-prompt one-shot inference
./tools/chat.sh --once "سلام! خودت را در دو جمله معرفی کن."
.\tools\chat.ps1 --once "سلام! خودت را در دو جمله معرفی کن."
```

#### Local Conversation Session CLI

```bash
python -m server.sessions list
python -m server.sessions show <session_id>
python -m server.sessions delete <session_id>
python -m server.sessions purge --days 30
```

---

### 3. Python API Inference

#### A. In-Process Python API (LLM, Local Whisper ASR, Persian TTS Prep & Piper)

```python
import asyncio
import threading
from pathlib import Path

from server.config import Settings
from server.live_voice import Speaker
from server.llm import LlamaServerClient
from server.persian import prepare_for_tts
from server.voice import LocalAudio


async def run_inference() -> None:
    settings = Settings.from_env()

    # 1. Local ASR (faster-whisper / CTranslate2; resamples WAV/audio locally to 16 kHz mono)
    asr = LocalAudio(settings)
    user_text = asr.transcribe(Path("sample_fa.wav"))
    print("User transcript:", user_text)

    # 2. Local LLM inference via running llama-server (async HTTP)
    llm = LlamaServerClient(settings)
    try:
        result = await llm.chat([{"role": "user", "content": user_text}])
        print("Assistant reply:", result.content)
        print("Timings:", result.timings.as_dict())
    finally:
        await llm.aclose()

    # 3. Normalize Persian text for natural speech and synthesize with local Piper
    spoken_text = prepare_for_tts(result.content)
    speaker = Speaker(settings)
    cancel = threading.Event()
    speaker.speak(spoken_text, cancel)


if __name__ == "__main__":
    asyncio.run(run_inference())
```

For streaming token-by-token output from `LlamaServerClient`:

```python
import asyncio
from server.config import Settings
from server.llm import LlamaServerClient, StreamDelta, StreamDone


async def stream_llm() -> None:
    settings = Settings.from_env()
    llm = LlamaServerClient(settings)
    try:
        async for event in llm.stream_chat([
            {"role": "user", "content": "سه نکته درباره یادگیری پایتون بگو."}
        ]):
            if isinstance(event, StreamDelta):
                print(event.content, end="", flush=True)
            elif isinstance(event, StreamDone):
                print("\nDone | tok/s:", event.result.timings.tokens_per_second)
    finally:
        await llm.aclose()


asyncio.run(stream_llm())
```

#### B. HTTP / OpenAI-Compatible API Client (`httpx`)

When the FastAPI server (`tools/start_server.sh` or `tools\start_server.ps1`) is running:

```python
import os
import httpx

BASE_URL = "http://127.0.0.1:8000"
TOKEN = os.getenv("PVAI_API_TOKEN", "")
HEADERS = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}

with httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=60.0) as client:
    # Simple session-aware endpoint
    r = client.post(
        "/api/chat",
        json={"message": "سلام! وضعیت هوا چطوره؟", "session_id": "demo-session"},
    )
    r.raise_for_status()
    print("Reply:", r.json()["reply"])

    # OpenAI-compatible chat completions endpoint
    r2 = client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "پایتخت ایران کجاست؟"}],
            "temperature": 0.7,
            "max_tokens": 128,
            "stream": False,
        },
    )
    r2.raise_for_status()
    print("OpenAI-compat:", r2.json()["choices"][0]["message"]["content"])
```

---

## REST & WebSocket API

| Endpoint | Purpose |
|---|---|
| `GET /health` | Minimal liveness probe (`{"status": "ok", ...}`). Always exempt from auth and intentionally omits sensitive config/paths. |
| `GET /ready` | Full readiness checklist (`configuration`, `asr_model`, `tts_model`, `dependencies`, `aec`, `storage`, `ws_voice`, `llm_backend`, `thinking`). Returns `503` if incomplete. |
| `GET /api/protocol` | ESP32-S3 WebSocket protocol descriptor (`transport_implemented: true`, `firmware_implemented: false`). |
| `WS /ws/voice` | LAN audio terminal WebSocket transport (opt-in via `PVAI_WS_ENABLED=true` and `PVAI_WS_TOKENS`). |
| `GET /v1/models` | Models currently loaded in `llama-server`. |
| `POST /v1/chat/completions` | OpenAI-compatible chat completion (JSON or SSE with `stream: true`). |
| `POST /api/chat` | Single-turn chat helper with optional persistent `session_id`. |
| `GET /api/sessions` / `GET\|DELETE /api/sessions/{id}` | List, inspect, or delete local JSONL conversation sessions. |

**Authentication & Network Safety:**
- When `PVAI_API_TOKEN` is set, all HTTP routes except `/health` (and `OPTIONS` preflight requests) require `Authorization: Bearer <token>` or `X-API-Key: <token>`.
- Leaving `PVAI_API_TOKEN` empty is permitted **only** on loopback addresses (`127.0.0.1` / `localhost` / `::1`); binding `PVAI_HOST=0.0.0.0` without a token is rejected at startup.
- WebSocket device transport uses independent per-device tokens (`PVAI_WS_TOKENS="device_id:token,..."`) and optional direct TLS (`PVAI_SSL_CERTFILE` + `PVAI_SSL_KEYFILE`).

---

## Directory Structure

```text
PersianVoiceAI/
├─ .env.example                 # Configuration template (copy to .env; never commit secrets)
├─ AGENTS.md                    # Engineering rules, repo map, and development contract
├─ LICENSE                      # GPL-3.0-or-later (required by in-process piper-tts)
├─ THIRD_PARTY.md               # Dependency and model weight licensing guide
├─ pytest.ini                   # Pytest discovery settings
├─ requirements.txt             # Minimal runtime dependencies (FastAPI, Uvicorn, httpx)
├─ requirements-voice.txt       # Optional local voice stack (Whisper, Piper, audio I/O)
├─ requirements-dev.txt         # Test/tooling dependencies (pytest, PyYAML, pyarrow)
├─ configs/                     # Dataset & training configuration examples (JSON + YAML)
├─ server/                      # Core Python backend & voice engine
│  ├─ main.py                   # FastAPI app, REST routes, auth middleware, rate limiting
│  ├─ __main__.py               # Entry point for `python -m server`
│  ├─ config.py                 # `Settings.from_env()` for all `PVAI_*` options & `--launch` CLI
│  ├─ llm.py                    # Async `LlamaServerClient` (JSON & SSE streaming)
│  ├─ streaming_voice.py        # Actor-based duplex pipeline, state machine, AsrScheduler, barge-in
│  ├─ live_voice.py             # `Speaker` (Piper TTS synthesis/playback) & sentence chunker
│  ├─ voice.py                  # `LocalAudio` (faster-whisper ASR) & `python -m server.voice` CLI
│  ├─ vad.py                    # `EnergyVAD` (hysteresis, pre-roll, adaptive noise floor)
│  ├─ aec.py                    # SpeexDSP ctypes wrapper & `DuplexAudio` stream reference
│  ├─ audio_io.py               # Sounddevice selection, validation, stall watchdog, reopen policy
│  ├─ conversation.py           # Token-budgeted local JSONL conversation memory & file locks
│  ├─ sessions.py               # CLI for listing, inspecting, deleting, and purging sessions
│  ├─ persian.py                # Persian evaluation normalization, WER/CER, & `prepare_for_tts`
│  ├─ metrics.py                # Bounded in-memory latency/queue/system metrics
│  ├─ resources.py              # Cross-platform CPU/RAM sampler (zero mandatory dependencies)
│  ├─ readiness.py              # Static & backend readiness diagnostics (`/ready`)
│  ├─ protocol.py               # Binary audio frame & JSON control message contract for ESP32-S3
│  ├─ ws_voice.py               # `WS /ws/voice` server transport, pacing, & shared model pool
│  ├─ security.py               # Token validation & rate-limiting helpers
│  └─ cli.py                    # Interactive Persian text chat CLI (`python -m server.cli`)
├─ tools/                       # Cross-platform launchers & operational utilities
│  ├─ start_llama.sh / .ps1     # Launch `llama-server` with flags generated from `.env`
│  ├─ start_server.sh / .ps1    # Launch the FastAPI server
│  ├─ chat.sh / .ps1            # Launch the interactive Persian text CLI
│  ├─ benchmark.sh / .ps1       # Run text LLM throughput/TTFT benchmark
│  ├─ benchmark_voice.py        # End-to-end voice pipeline benchmark (MEASURED/SIMULATED/NOT MEASURED)
│  ├─ download_parallel.py      # Resumable parallel HTTPS downloader with mandatory SHA-256 checks
│  ├─ prepare_dataset.py        # Manifest validator (JSON/JSONL/CSV/Hugging Face) + portable export
│  ├─ train_plan.py             # Validates a training config and emits a plan for an external trainer
│  ├─ training_config.py        # Shared JSON/YAML config loader: paths, types, ranges, typo rejection
│  ├─ environment_test.py       # Quick check for Python 3.12 inside `.venv`
│  └─ build_docs.py             # Builds and checks `docs/PROJECT_GUIDE.md`
├─ benchmarks/                  # Evaluation scripts, prompt sets, and JSON results
│  ├─ bench_chat.py             # Text chat latency & tok/s benchmark
│  ├─ prompts_fa.json           # Persian text benchmark prompts
│  ├─ persian_eval.py           # Persian ASR WER/CER & no-speech false-accept evaluator
│  ├─ persian_eval_set.json     # 40-sentence Persian + 6 no-speech evaluation dataset
│  └─ results/                  # Saved benchmark output snapshots
├─ tests/                       # Model-free pytest suite using fake LLM/ASR/TTS/audio devices
├─ docs/                        # Technical docs (architecture, AEC, protocol, config, troubleshooting)
│  ├─ PROJECT_GUIDE.md          # Auto-generated single-file Persian project guide
│  └─ legacy/                   # Archived Lesson 1 / Ollama setup scripts & logs (deprecated)
├─ models/                      # (Local, git-ignored) Place downloaded GGUF, Whisper & Piper weights here
├─ data/                        # (Local, git-ignored) Local conversation logs (`data/conversations/*.jsonl`)
└─ firmware/                    # (Planned, not yet created) Future ESP32-S3 ESP-IDF terminal firmware
```

---

## Testing, Benchmarks & Dataset Validation

### Running Automated Tests

All tests use fakes/mocks and require **no model weights, microphone, or network access**.

```bash
# Install voice + dev requirements to run the complete suite (including audio conversion tests)
python -m pip install -r requirements-voice.txt -r requirements-dev.txt

# Run tests and inspect any skipped tests (e.g., native SpeexDSP tests when libspeexdsp is absent)
python -m pytest -rs
```

*(Note: `tests/test_inference_safety.py` imports `numpy` and `soundfile` at module level; without `requirements-voice.txt`, pass `--ignore=tests/test_inference_safety.py` to run the core non-audio tests.)*

### Benchmarks & Persian ASR Evaluation

Every metric reported by the project's benchmark tooling is explicitly tagged `MEASURED`, `SIMULATED`, or `NOT MEASURED` (see [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md)):

```bash
# End-to-end voice pipeline benchmark (measures available local components, labels missing ones)
python tools/benchmark_voice.py --json-out benchmarks/results/voice-pc.json

# List the 40-sentence Persian ASR evaluation set
python benchmarks/persian_eval.py --list

# Evaluate WER/CER against real local WAV recordings using local Whisper
python benchmarks/persian_eval.py --audio-dir recordings/fa --strict

# Legacy text-only LLM benchmark
./tools/benchmark.sh      # Linux / macOS
```

```powershell
# Legacy text-only LLM benchmark (Windows PowerShell)
.\tools\benchmark.ps1
```

### Custom Datasets & External Training Contract

This repository is for inference and evaluation only and **does not include a training loop**. Data preparation and the hyper-parameters handed to an *external* trainer are nonetheless validated and reproducible:

```bash
# 1. Describe the corpus once (JSON or YAML; paths may use ${VAR} and are
#    resolved relative to the config file, never the shell's cwd)
cp configs/dataset.example.yaml configs/dataset.yaml
export PVAI_CORPUS_ROOT=/data/persian-corpus

# 2. Validate every split, get a split-leakage report, and export portable JSONL
python tools/prepare_dataset.py --config configs/dataset.yaml --check   --report reports/dataset.json

# 3. Validate the training hyper-parameters and hand the trainer a plan
python tools/train_plan.py --config configs/training.example.yaml --check --no-require-paths
PVAI_CORPUS_ROOT=/data/persian-corpus python tools/train_plan.py   --config configs/training.yaml --write runs/whisper-small-fa/plan.json
```

Both tools also work purely from flags (`--manifest`, `--audio-root`, `--batch-size`, `--learning-rate`, …); a config file simply removes the machine-specific paths from the command line. Inputs: `.json`, `.jsonl`, `.csv`, or a Hugging Face `Dataset.save_to_disk` directory whose `audio` column holds path strings. Nothing is downloaded, nothing is rewritten, and no training is performed.

See [`docs/DATASETS.md`](docs/DATASETS.md) for the manifest schema, split hygiene, Hugging Face input, and the full list of validated hyper-parameters.

---

## Honest Status & Known Limitations

- **Tests use fakes**: The `pytest` suite verifies orchestration, state transitions, cancellation, security, and docs consistency without real Whisper/Piper/Qwen weights or physical audio devices. Real model/device behavior is **NOT MEASURED** in the sandbox.
- **Native AEC requires calibration**: SpeexDSP AEC has been verified on synthetic echo/double-talk signals (`57.8 dB` echo reduction in a synthetic test), not yet in real room acoustics. When using speakers without headphones, calibrate `PVAI_AEC_DELAY_MS` on target hardware.
- **Incremental ASR**: Overlapping-window Whisper partial transcription (`PVAI_ASR_PARTIAL_WINDOW_S`) is an adapter over `faster-whisper`, not a natively streaming ASR model.
- **ESP32-S3 firmware not yet implemented**: The server-side WebSocket transport (`WS /ws/voice`) is implemented and tested in-process with fakes, but ESP32-S3 firmware and live LAN tests have **not** been implemented or tested (`/api/protocol` reports `transport_implemented: true`, `firmware_implemented: false`).
- **Local conversation memory**: History persists locally in `data/conversations/*.jsonl` with a bounded RAM window and 30-day default retention (`PVAI_HISTORY_RETENTION_DAYS`).
- **Default execution is CPU**: Historical GPU notes on `MiniCPM` do not generalize to `Qwen3-4B`; real `Qwen3-4B` / Whisper / Piper target latencies remain **NOT MEASURED**.

---

## Documentation Index

| Document | Description |
|---|---|
| [`docs/PROJECT_GUIDE.md`](docs/PROJECT_GUIDE.md) | Auto-generated single-file Persian guide (`python tools/build_docs.py`) |
| [`AGENTS.md`](AGENTS.md) | Engineering rules, development contract, and module map |
| [`docs/SPEC.md`](docs/SPEC.md) | System architecture, hardware target, and design decisions |
| [`docs/STATUS.md`](docs/STATUS.md) | Implementation status & 20-requirement test traceability matrix |
| [`docs/PC_VOICE.md`](docs/PC_VOICE.md) | PC live voice installation, sensitivity tuning, and constraints |
| [`docs/STREAMING_ARCHITECTURE.md`](docs/STREAMING_ARCHITECTURE.md) | Utterance/turn lifecycles, queues, state machine, and cancellation |
| [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) | Complete reference for all `PVAI_*` environment variables |
| [`docs/AEC.md`](docs/AEC.md) | SpeexDSP native setup, delay calibration, and speaker/mic checklist |
| [`docs/PROTOCOL.md`](docs/PROTOCOL.md) | ESP32-S3 WebSocket binary/JSON protocol & server transport |
| [`docs/DATASETS.md`](docs/DATASETS.md) | Custom dataset contract and `tools/prepare_dataset.py` guide |
| [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) | Benchmarking methodology, sandbox results, and Persian WER/CER evaluation |
| [`docs/HARDWARE_VALIDATION.md`](docs/HARDWARE_VALIDATION.md) | Real-hardware acceptance checklist |
| [`docs/PLATFORMS.md`](docs/PLATFORMS.md) | Windows / Linux / macOS script parity and path differences |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | Error messages, causes, and fixes |
| [`docs/ISSUE_REGISTER.md`](docs/ISSUE_REGISTER.md) | Issue log with root causes, fixes, and regression tests |
| [`THIRD_PARTY.md`](THIRD_PARTY.md) | Dependency licenses, model cards, and SHA-256 verification notes |

---

## License

This repository's code is licensed under **GPL-3.0-or-later** ([`LICENSE`](LICENSE)), required because `piper-tts` (`GPL-3.0-or-later`) is imported in-process by `server/live_voice.py`. See [`THIRD_PARTY.md`](THIRD_PARTY.md) for full details and alternative architectural options.

Model weights are not stored in this repository and retain their respective licenses:

- **Qwen3-4B**: Apache-2.0
- **Whisper `small` weights**: MIT
- **Piper Persian voices**: Varies per voice file — always verify the accompanying `MODEL_CARD` / `LICENSE` before redistribution.
