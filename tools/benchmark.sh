#!/usr/bin/env bash
# Persian chat latency/throughput benchmark. Bash equivalent of tools/benchmark.ps1.
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

exec "$PYTHON" benchmarks/bench_chat.py "$@"
