#!/usr/bin/env bash
# Local one-turn test bench: mic/text → Whisper → llama-server → Piper in the browser.
# Loopback only by default; see docs/WEB_UI.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PVAI_PYTHON:-$ROOT/.venv/bin/python}"
exec "$PYTHON" -m server.webui "$@"
