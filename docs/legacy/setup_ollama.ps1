param(
    [string]$Model = "qwen3:4b",
    [string]$ModelsDir = "D:\PersianVoiceAI\models\ollama"
)

$ErrorActionPreference = "Stop"

function Find-OllamaExe {
    $paths = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"),
        (Join-Path $env:ProgramFiles "Ollama\ollama.exe"),
        (Get-Command ollama.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
    )
    foreach ($path in $paths) {
        $trimmed = [string]$path
        if ($trimmed -and (Test-Path $trimmed)) { return $trimmed }
    }
    throw "Ollama not found. Install it first (see README)."
}

$ollamaExe = Find-OllamaExe
Write-Host "Ollama: $ollamaExe" -ForegroundColor Cyan

if (-not (Test-Path $ModelsDir)) {
    New-Item -ItemType Directory -Force -Path $ModelsDir | Out-Null
}

$userModels = [Environment]::GetEnvironmentVariable("OLLAMA_MODELS", "User")
$env:OLLAMA_MODELS = $ModelsDir
if ($userModels -ne $ModelsDir) {
    [Environment]::SetEnvironmentVariable("OLLAMA_MODELS", $ModelsDir, "User")
    Write-Host "OLLAMA_MODELS set (User scope): $ModelsDir" -ForegroundColor Green
}

$isRunning = Test-NetConnection -ComputerName 127.0.0.1 -Port 11434 -InformationLevel Quiet -WarningAction SilentlyContinue

if (-not $isRunning) {
    Write-Host "Starting ollama serve (hidden)..." -ForegroundColor Cyan
    Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Hidden
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Seconds 1
        if (Test-NetConnection -ComputerName 127.0.0.1 -Port 11434 -InformationLevel Quiet -WarningAction SilentlyContinue) {
            $isRunning = $true
            break
        }
    }
}

if (-not $isRunning) {
    throw "Ollama did not come up on 127.0.0.1:11434. Check firewall or run 'ollama serve' manually."
}

Write-Host "Ollama is running, models dir: $env:OLLAMA_MODELS" -ForegroundColor Green

Write-Host "Pulling model $Model (this can take a while)..." -ForegroundColor Cyan
try {
    $process = Start-Process -FilePath $ollamaExe -ArgumentList @('pull', $Model) -NoNewWindow -PassThru -RedirectStandardOutput "$env:TEMP\ollama_pull.log" -RedirectStandardError "$env:TEMP\ollama_pull.err.log"
    $process.WaitForExit() | Out-Null
    if ($process.ExitCode -ne 0) {
        Get-Content "$env:TEMP\ollama_pull.err.log" -ErrorAction SilentlyContinue
        throw "ollama pull failed."
    }
} finally {
    if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
}

Write-Host "Available models:" -ForegroundColor Green
& $ollamaExe list
