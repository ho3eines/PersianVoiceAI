#!/usr/bin/env bash
# Install SpeexDSP from the system package manager (POSIX equivalent of
# tools/install_speexdsp.ps1, which pulls a conda-forge package on Windows).
#
#   ./tools/install_speexdsp.sh              # try apt/dnf/pacman/brew in turn
#   sudo ./tools/install_speexdsp.sh         # most package managers need root
#
# PVAI_PYTHON lets a caller point at an interpreter outside the checkout.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PVAI_PYTHON:-$ROOT/.venv/bin/python}"

if command -v apt-get >/dev/null 2>&1; then
    apt-get install -y libspeexdsp-dev
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y speexdsp-devel
elif command -v pacman >/dev/null 2>&1; then
    pacman -Sy --needed speexdsp
elif command -v brew >/dev/null 2>&1; then
    brew install speexdsp
else
    echo "no supported package manager found (apt/dnf/pacman/brew)" >&2
    exit 1
fi

# Verify the shared library actually loads, the same way server/aec.py does.
if [[ -x "$PYTHON" ]]; then
    "$PYTHON" - <<'PY'
import ctypes

try:
    ctypes.CDLL("libspeexdsp.so.1")
except OSError:
    ctypes.CDLL("libspeexdsp.dylib")
except OSError:
    ctypes.CDLL("libspeexdsp.so")
print("libspeexdsp loaded OK")
PY
else
    echo "PVAI_PYTHON not found ($PYTHON); skipping load check" >&2
fi
