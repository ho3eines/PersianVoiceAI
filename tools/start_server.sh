#!/usr/bin/env bash
# Start the Persian Voice AI API. Bash equivalent of tools/start_server.ps1.
#
#   ./tools/start_server.sh                      # 127.0.0.1:8000
#   ./tools/start_server.sh --bind 0.0.0.0 --port 9000
#   ./tools/start_server.sh --model /path/to/model.gguf
set -euo pipefail

BIND=""
PORT=""
MODEL_PATH=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bind)  BIND="${2:?--bind needs a value}"; shift 2 ;;
        --port)  PORT="${2:?--port needs a value}"; shift 2 ;;
        --model) MODEL_PATH="${2:?--model needs a value}"; shift 2 ;;
        -h|--help) sed -n "2,6p" "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# PVAI_PYTHON lets a caller point at an interpreter outside the checkout.
PYTHON="${PVAI_PYTHON:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
    echo "Virtual environment not found: $PYTHON" >&2
    echo "Create it with: python3 -m venv .venv" >&2
    exit 1
fi

[[ -n "$MODEL_PATH" ]] && export PVAI_MODEL_PATH="$MODEL_PATH"
[[ -n "$BIND" ]] && export PVAI_HOST="$BIND"
[[ -n "$PORT" ]] && export PVAI_PORT="$PORT"

BIND="${BIND:-127.0.0.1}"
PORT="${PORT:-8000}"

SCHEME="http"
if [[ -n "${PVAI_SSL_CERTFILE:-}" && -n "${PVAI_SSL_KEYFILE:-}" ]]; then
    SCHEME="https"
fi
echo "Persian Voice AI API (llama.cpp) -> $SCHEME://$BIND:$PORT"
echo "Docs: $SCHEME://$BIND:$PORT/docs"

exec "$PYTHON" -m server
