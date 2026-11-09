#!/usr/bin/env bash
# Interactive Persian chat client. Bash equivalent of tools/chat.ps1.
#
#   ./tools/chat.sh
#   ./tools/chat.sh --once "سلام! خودت را معرفی کن"
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# PVAI_PYTHON lets a caller point at an interpreter outside the checkout.
PYTHON="${PVAI_PYTHON:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
    echo "Virtual environment not found: $PYTHON" >&2
    echo "Create it with: python3 -m venv .venv" >&2
    exit 1
fi

exec "$PYTHON" -m server.cli "$@"
