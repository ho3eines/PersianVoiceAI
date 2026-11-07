param(
    [string]$Bind = "",
    [int]$Port = 0,
    [string]$ModelPath = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Virtual environment not found. Create it with: py -3.12 -m venv .venv"
}

if ($ModelPath) { $env:PVAI_MODEL_PATH = $ModelPath }
if ($Bind) { $env:PVAI_HOST = $Bind }
if ($Port -gt 0) { $env:PVAI_PORT = "$Port" }

$bind = if ($Bind) { $Bind } else { "127.0.0.1" }
$port = if ($Port -gt 0) { $Port } else { 8000 }

Write-Host "Persian Voice AI API (llama.cpp) -> http://${bind}:${port}" -ForegroundColor Cyan
Write-Host "Docs: http://${bind}:${port}/docs" -ForegroundColor DarkGray

& $python -m server