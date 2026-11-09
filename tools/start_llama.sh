#!/usr/bin/env bash
# Start llama-server with exactly the flags implied by .env.
# Bash equivalent of tools/start_llama.ps1.
#
#   ./tools/start_llama.sh                          # llama-server from PATH
#   ./tools/start_llama.sh --exe /opt/llama.cpp/llama-server
#   ./tools/start_llama.sh --dry-run                # print the command only
#
# The command line comes from `python -m server.config --launch`, so
# PVAI_MODEL_PATH / PVAI_N_CTX / PVAI_N_THREADS / PVAI_N_GPU_LAYERS /
# PVAI_LLAMA_SERVER_URL are honoured instead of drifting in documentation.
set -euo pipefail

EXE="llama-server"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --exe) EXE="${2:?--exe needs a value}"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) sed -n "2,11p" "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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

# Keep the JSON in a file: the plan is a list, and unzipping it in bash after
# the fact is how quoting bugs get in.
PLAN_FILE="$(mktemp)"
trap 'rm -f "$PLAN_FILE"' EXIT

if ! "$PYTHON" -m server.config --launch-json "$EXE" > "$PLAN_FILE"; then
    echo "python -m server.config --launch failed" >&2
    exit 1
fi

# Read the plan with the project's own interpreter rather than requiring jq.
eval "$("$PYTHON" - "$PLAN_FILE" <<'PY'
import json, shlex, sys
plan = json.load(open(sys.argv[1]))
print("COMMAND=(" + " ".join(shlex.quote(part) for part in plan["command"]) + ")")
print("MODEL_PATH=" + shlex.quote(plan["model_path"]))
print("N_CTX=%d N_THREADS=%d N_GPU_LAYERS=%d" % (
    plan["n_ctx"], plan["n_threads"], plan["n_gpu_layers"]))
print("WARNINGS=(" + " ".join(
    shlex.quote("%s: %s" % (i["variable"], i["reason"])) for i in plan["env_issues"]) + ")")
PY
)"

if [[ "${COMMAND[0]}" == "llama-server" && "$EXE" == "llama-server" ]]; then
    # Resolve a bare name through PATH so a missing binary fails here with a
    # readable message instead of bash's "command not found".
    RESOLVED="$(command -v llama-server || true)"
    if [[ -z "$RESOLVED" ]]; then
        echo "llama-server not found ('$EXE'). Pass --exe with the full path." >&2
        exit 1
    fi
    COMMAND[0]="$RESOLVED"
fi

echo "llama-server plan:"
echo "  ${COMMAND[*]}"
echo "  n_ctx=$N_CTX n_threads=$N_THREADS n_gpu_layers=$N_GPU_LAYERS"

for issue in ${WARNINGS[@]+"${WARNINGS[@]}"}; do
    echo "warning: $issue"
done

if [[ ! -f "$MODEL_PATH" ]]; then
    echo "warning: model file not found: $MODEL_PATH"
fi

if [[ $DRY_RUN -eq 1 ]]; then
    echo "DryRun: not starting the server."
    exit 0
fi

if [[ ! -f "$MODEL_PATH" ]]; then
    echo "Model weights not found: $MODEL_PATH (set PVAI_MODEL_PATH)." >&2
    exit 1
fi

exec "${COMMAND[@]}"
