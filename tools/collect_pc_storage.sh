#!/usr/bin/env bash
# Collect physical-disk and mounted-filesystem info before model downloads.
# Bash equivalent of tools/collect_pc_storage.ps1 (Windows uses Get-PhysicalDisk /
# Win32_LogicalDisk there; POSIX uses lsblk / df here).
#
#   ./tools/collect_pc_storage.sh            # human-readable tables
#   ./tools/collect_pc_storage.sh --json     # JSON via the venv python
#
# PVAI_PYTHON lets a caller point at an interpreter outside the checkout.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PVAI_PYTHON:-$ROOT/.venv/bin/python}"

if [[ "${1:-}" == "--json" ]]; then
    if [[ ! -x "$PYTHON" ]]; then
        echo "python interpreter not found: $PYTHON (set PVAI_PYTHON)" >&2
        exit 1
    fi
    exec "$PYTHON" - <<'PY'
import json
import subprocess

def run(cmd):
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return json.loads(out)

lsblk = run(["lsblk", "--json", "-d", "-o", "NAME,ROTA,TRAN,SIZE,TYPE"])
df = run(["df", "-h", "-x", "tmpfs", "-x", "devtmpfs", "-x", "efivarfs", "--output=source,fstype,size,used,avail,pcent,target"])
print(json.dumps({"physical_disks": lsblk.get("blockdevices", []), "filesystems": df}, ensure_ascii=False, indent=2))
PY
else
    echo "== Physical disks =="
    lsblk -d -o NAME,ROTA,TRAN,SIZE,TYPE 2>/dev/null || lsblk -d -o NAME,SIZE,TYPE
    echo
    echo "== Mounted filesystems =="
    df -h -x tmpfs -x devtmpfs -x efivarfs
fi
