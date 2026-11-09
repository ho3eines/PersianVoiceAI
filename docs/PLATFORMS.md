# Platform support

The Python code is portable: it is developed and tested on Linux with no
platform-specific imports, and runs on Windows (the original target), Linux and
macOS. The gap was never the code — it was the tooling, which shipped only
PowerShell. That is now fixed, with two deliberate exceptions listed below.

## What runs where

| Area | Windows | Linux / macOS | Notes |
|---|---|---|---|
| Server, REST, WebSocket | yes | yes | `python -m server` / `uvicorn server.main:app` |
| Chat client | yes | yes | `python -m server.cli` |
| Test suite | yes | yes | 560 pass in both; 5 skips explained in `docs/STATUS.md` |
| llama-server launcher | `tools/start_llama.ps1` | `tools/start_llama.sh` | both read `python -m server.config --launch` |
| Server launcher | `tools/start_server.ps1` | `tools/start_server.sh` | |
| Chat launcher | `tools/chat.ps1` | `tools/chat.sh` | |
| Chat benchmark | `tools/benchmark.ps1` | `tools/benchmark.sh` | |
| Large-file download | `tools/download_parallel.py` | `tools/download_parallel.py` | one Python implementation, both platforms |
| SpeexDSP AEC | needs a built DLL | needs a built `.so` | see `docs/AEC.md`; not a pip install |
| Audio I/O | WASAPI via `sounddevice` | ALSA/PulseAudio via `sounddevice` | device names differ; check `/ready` |

Every `.ps1` in `tools/` has a `.sh` twin, and
`tests/test_tools_scripts.py` fails if a new unmatched one appears.

## Choosing the interpreter

The shell scripts look for `.venv` next to the repository. Set `PVAI_PYTHON` to
point somewhere else:

```bash
PVAI_PYTHON=/opt/pvai/bin/python ./tools/start_server.sh
```

## Legacy (not on supported launch path)

Old Ollama setup, lesson-1 verification and its historic report are archived under
`docs/legacy/`. Use the current llama.cpp launchers and benchmark instead.

## Path and environment differences

- Paths in `.env` may be absolute or relative to the repository root;
  `server/config.py` resolves them, so `/opt/models/x.gguf` and `models/x.gguf`
  both work.
- `llama-server` is `llama-server` on both platforms; pass `--exe` / `-Exe` when
  it is not on `PATH`.
- The `.env` loader skips a leading UTF-8 BOM, because Windows editors add one.

## What is *not* verified

Real audio, real models and real microphones have not been exercised on either
platform in this environment — see `docs/HARDWARE_VALIDATION.md`. "Runs on
Linux" here means the test suite passes and the scripts parse and execute, not
that a Persian conversation has been held on a Linux desktop.
