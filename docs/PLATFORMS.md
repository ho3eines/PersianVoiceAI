# Platform support

Windows, Linux and macOS are intended platforms. Both PowerShell and Bash launchers
are maintained, and the automated suite has run on Windows and Linux. See
[STATUS](STATUS.md) and [Hardware Validation](HARDWARE_VALIDATION.md) for evidence
and limits beyond the automated suite.

## What runs where

| Area | Windows | Linux / macOS | Notes |
|---|---|---|---|
| Server, REST, WebSocket | yes | yes | `python -m server` / `uvicorn server.main:app` |
| Chat client | yes | yes | `python -m server.cli` |
| Test suite | yes | yes | Run `python -m pytest -rs`; macOS execution is not recorded here |
| llama-server launcher | `tools/start_llama.ps1` | `tools/start_llama.sh` | both read `python -m server.config --launch` |
| Server launcher | `tools/start_server.ps1` | `tools/start_server.sh` | |
| Chat launcher | `tools/chat.ps1` | `tools/chat.sh` | |
| Chat benchmark | `tools/benchmark.ps1` | `tools/benchmark.sh` | |
| Web test bench | `tools/start_webui.ps1` | `tools/start_webui.sh` | Loopback only; see [WEB_UI.md](WEB_UI.md) |
| Large-file download | `tools/download_parallel.py` | `tools/download_parallel.py` | one Python implementation, both platforms |
| SpeexDSP AEC | needs a built DLL | needs a built `.so` / `.dylib` | see `docs/AEC.md`; not a pip install |
| Audio I/O | PortAudio via `sounddevice` (e.g. MME) | PortAudio via `sounddevice` | Use a device that accepts the 16 kHz live format; list with `python -m server.voice --list-devices` |

Every `.ps1` in `tools/` has a `.sh` twin, and
`tests/test_tools_scripts.py` fails if a new unmatched one appears.

## Choosing the interpreter

The shell scripts look for `.venv` next to the repository. For bash scripts, set `PVAI_PYTHON` to
point somewhere else (PowerShell scripts use `.venv\Scripts\python.exe`):

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

The development Windows laptop has dated measurements of microphone capture, model
inference, and room AEC. They do not establish continuous voice acceptance, barge-in
with human speech, or real ESP32/LAN behavior. Linux/macOS audio acceptance is not
documented; passing the suite does not establish that real audio devices work.
