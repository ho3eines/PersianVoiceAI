param(
    [string]$Exe = "llama-server",
    [switch]$DryRun
)

# Start llama-server with exactly the flags implied by .env.
#
# The command line is produced by `python -m server.config --launch`, so
# PVAI_MODEL_PATH / PVAI_N_CTX / PVAI_N_THREADS / PVAI_N_GPU_LAYERS /
# PVAI_LLAMA_SERVER_URL are honoured instead of being restated (and drifting)
# in documentation. `-DryRun` prints the command without starting anything.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Virtual environment not found. Create it with: py -3.12 -m venv .venv"
}

$launch = & $python -m server.config --launch-json $Exe
if ($LASTEXITCODE -ne 0) { throw "python -m server.config --launch failed" }
$plan = $launch | ConvertFrom-Json

$command = $plan.command
if ($command[0] -eq "llama-server") {
    # Resolve a bare name through PATH so a missing binary fails here with a
    # readable message instead of a PowerShell "term is not recognized" dump.
    $found = Get-Command $Exe -ErrorAction SilentlyContinue
    if (-not $found) {
        throw "llama-server not found ('$Exe'). Pass -Exe with the full path (e.g. -Exe D:\Lama.cpp\bin\llama-server.exe)."
    }
    $command[0] = $found.Source
}

Write-Host "llama-server plan:" -ForegroundColor Cyan
Write-Host ("  " + ($command -join " ")) -ForegroundColor DarkGray
Write-Host ("  n_ctx={0} n_threads={1} n_gpu_layers={2}" -f $plan.n_ctx, $plan.n_threads, $plan.n_gpu_layers) -ForegroundColor DarkGray

foreach ($issue in $plan.env_issues) {
    Write-Host ("warning: {0}: {1}" -f $issue.variable, $issue.reason) -ForegroundColor Yellow
}

$model = $plan.model_path
if (-not (Test-Path -LiteralPath $model -PathType Leaf)) {
    Write-Host "warning: model file not found: $model" -ForegroundColor Yellow
}

if ($DryRun) {
    Write-Host "DryRun: not starting the server." -ForegroundColor Yellow
    exit 0
}

if (-not (Test-Path -LiteralPath $model -PathType Leaf)) {
    throw "Model weights not found: $model (set PVAI_MODEL_PATH)."
}

& $command[0] $command[1..($command.Length - 1)]
