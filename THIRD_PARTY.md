# Third-party components and model licences

This project is distributed under **GPL-3.0-or-later** (`LICENSE`). That choice is
not arbitrary: `piper-tts` is GPL-3.0-or-later and is imported **in-process** by
`server/live_voice.py`, so a permissive or proprietary licence for this
repository would be incompatible with it. See *Why GPL* below.

No model weights are stored in this repository (`models/` is git-ignored). Every
model is downloaded by the operator, and the operator is bound by the licence of
whatever they download. The table records the default and the licence verified
for it, plus the trap cases.

## Python dependencies

Verified from PyPI package metadata and the upstream repositories.

| Package | Version | Licence | Notes |
|---|---|---|---|
| `fastapi` | 0.162.0 | MIT | |
| `pydantic` | 2.14.3 | MIT | runtime, used directly by `server/main.py` |
| `uvicorn[standard]` | 0.54.0 | BSD-3-Clause | `[standard]` pulls `uvloop` (MIT), `httptools` (MIT), `websockets` (BSD-3-Clause), `watchfiles` (MIT), `python-dotenv` (BSD-3-Clause), `PyYAML` (MIT) |
| `httpx` | 0.28.1 | BSD-3-Clause | runtime, used by `server/cli.py` and `server/llm.py` |
| `pytest` | 9.2.0 | MIT | **dev only** (`requirements-dev.txt`) |
| `PyYAML` | >=6,<7 | MIT | **dev/tooling only**: YAML configuration for `tools/training_config.py`; JSON configs work without it (also pulled by `uvicorn[standard]`) |
| `pyarrow` | >=14,<27 | Apache-2.0 | **dev/tooling only**: reads a Hugging Face `Dataset.save_to_disk` directory in `tools/prepare_dataset.py`; every runtime path uses JSONL/CSV instead |
| `starlette` | 1.10.0 | BSD-3-Clause | **dev/test direct import** (`tests/test_ws_voice.py`; also pulled by `fastapi`) |
| `faster-whisper` | >=1.1,<2 | MIT | optional voice stack |
| `ctranslate2` | >=4.0,<5 | MIT | optional voice stack (direct CUDA check in `server/voice.py`) |
| `piper-tts` | >=1.3,<2 | **GPL-3.0-or-later** | optional voice stack; the reason for this project's licence |
| `sounddevice` | >=0.5,<1 | MIT | optional voice stack |
| `soundfile` | >=0.13,<1 | BSD-3-Clause | optional voice stack |
| `numpy` | >=1.26.4,<2.6 | BSD-3-Clause and others | optional voice stack (upper-bounded for `numba`/`librosa` compatibility) |
| `scipy` | >=1.11,<2 | BSD-3-Clause | optional voice stack |
| `librosa` | >=0.10.2,<1 | ISC | optional voice stack; local file decoding/downmix/resampling |

`faster-whisper` in turn depends on `ctranslate2` (MIT) and `onnxruntime` (MIT),
which `piper-tts` also uses.

SpeexDSP (used by `server/aec.py` when built) is BSD-3-Clause; its `COPYING` and
`NOTICE` obligations are described in `docs/AEC.md` and must be honoured when
shipping a binary that links it.

## Model weights

| Model | Default path | Licence | SHA-256 of exact weight | Notes |
|---|---|---|---|---|
| Qwen3-4B (GGUF) | `models/Qwen3-4B-Q4_K_M.gguf` | Apache-2.0 | NOT VERIFIED (revision-dependent) | Apache-2.0 is GPLv3-compatible; safe to ship alongside this code. Quantised re-uploads inherit the base licence. |
| Whisper `small` weights | `models/whisper-small` | MIT | NOT VERIFIED (conversion-dependent) | OpenAI releases both code and weights under MIT; `faster-whisper` conversions keep it. |
| Piper Persian voice | `models/piper/fa_IR.onnx` | **varies per voice — check** | NOT VERIFIED (voice-dependent) | The voice file is a separate work from the Piper engine. See below. |

### The Piper voice licence trap

The engine's GPL licence says nothing about the **voice** you load into it. Each
Piper voice carries the licence of the recordings it was trained on, and several
Persian voices are not licensed for commercial use.

- The `fa_IR` models published in `rhasspy/piper-voices` (for example
  `fa_IR/amir/medium`) are documented on their model card as fine-tuned on a
  dataset released under **CC0**, which would permit commercial use — but the
  card is the authoritative statement, not this document.
- Third-party Persian Piper voices exist (for example `MahtaFetrat/Mana-Persian-Piper`,
  MIT, fine-tuned on the Mana-TTS dataset) and carry their own terms.
- Some vendored Persian voices are explicitly distributed for
  **personal, non-commercial use only**.

Vendors disagree on this, so **do not treat any summary — including this one — as
the licence**. Before shipping a voice, download it and read the `MODEL_CARD`
and any `LICENSE` that ships next to the `.onnx` file. Record the voice, its
source URL and its licence in your deployment notes.

`docs/SPEC.md` §۷ already lists "commercial use licence" as a selection criterion
for the ASR/TTS models and warns that the ParSiGo dataset licence must be
re-verified at the source. That warning still stands.

### Dataset licences

Fine-tuning data is not distributed here. If you fine-tune or redistribute a
model, the dataset licence travels with the derived weights: Mana-TTS and
datacula's Persian TTS databases each state their own terms on their project
pages.

## Why GPL

`server/live_voice.py` runs `dependency("piper.voice").PiperVoice.load(...)`
inside this process, and `server/voice.py` can also launch `python -m piper` as a
subprocess. Both are uses that the FSF and the Piper maintainers treat as
forming a combined work, so distributing this program together with
`piper-tts` obliges the combined distribution to be GPL-3.0-or-later.

If a permissive or closed-source licence is ever required, the options are:

1. Drop `piper-tts` and speak through a different engine, or
2. Keep the voice stack strictly separate and communicate with it only over a
   process boundary that the project documents as a separate program
   (an arms-length interface, which needs its own legal review), or
3. Pin the MIT-era `rhasspy/piper`, which was archived in October 2025 and no
   longer receives fixes — a deliberate trade of maintenance for licence.

Option 1 is the only one that does not depend on a legal judgement call.

## Regenerating the checks

Versions above were read from the installed environment and PyPI metadata:

```bash
python - <<'PY'
import json, urllib.request
for name in ("piper-tts", "faster-whisper", "fastapi", "uvicorn", "httpx", "pytest"):
    info = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json"))["info"]
    classifiers = [c for c in info.get("classifiers") or [] if c.startswith("License")]
    print(f"{name:16} {info.get('license_expression') or classifiers}")
PY
```

Model licences must be read from each model's card on its hosting page; they are
not machine-readable in a way that can be trusted here.

## Checksums for externally downloaded weights

No fixed model SHA-256 is asserted by this repository: a GGUF quantisation,
Whisper conversion or Piper voice revision changes its digest. Hashes are
**NOT VERIFIED** without an exact publisher URL/revision. Obtain the published
SHA-256 for the exact file from the model publisher (or a separately trusted
signed manifest), verify its licence/model card above, then pass that digest via
`tools/download_parallel.py --url https://... --output models/... --sha256 <64-hex>`.
The downloader checks bytes **before** replacing the destination. A checksum
copied from the same untrusted download endpoint does not authenticate a model.
`model.bin`, `.gguf` and `.onnx` require `--sha256`; TLS is required except
loopback-only test sources. Do not claim a model's licence from its extension.
