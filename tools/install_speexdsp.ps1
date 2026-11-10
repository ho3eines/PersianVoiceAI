param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
$packageUrl = "https://conda.anaconda.org/conda-forge/win-64/speexdsp-1.2.1-h5112557_0.conda"
$expectedSha256 = "064be2a5ea3e168a20d1a8319d853310f3cc3e90bd99f5a0bc2b5e15e2d91b1c"
$sevenZip = Get-Command 7z.exe -ErrorAction SilentlyContinue
if (-not $sevenZip) {
    throw "7-Zip is required and was not found on PATH."
}

$stageRoot = Join-Path $env:TEMP ("pvai-speex-" + [guid]::NewGuid().ToString("N"))
$package = Join-Path $stageRoot "speexdsp.conda"
$unpacked = Join-Path $stageRoot "unpacked"
$target = Join-Path $RepoRoot "models\speexdsp"
$dll = Join-Path $target "Library\bin\speexdsp.dll"
if (Test-Path -LiteralPath $dll) {
    Write-Output "SpeexDSP is already installed: $dll"
    return
}
New-Item -ItemType Directory -Force -Path $unpacked, $target | Out-Null

try {
    Invoke-WebRequest -Uri $packageUrl -OutFile $package
    $actualSha256 = (Get-FileHash -LiteralPath $package -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualSha256 -ne $expectedSha256) {
        throw "SpeexDSP package SHA256 mismatch: $actualSha256"
    }

    & $sevenZip.Source x -y "-o$unpacked" $package | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to extract the conda archive." }
    $payload = Get-ChildItem -LiteralPath $unpacked -Filter "pkg-*.tar.zst" | Select-Object -First 1
    if (-not $payload) { throw "Package payload was not found in the archive." }
    & $sevenZip.Source x -y "-o$unpacked" $payload.FullName | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to extract the zstd payload." }
    $tarFile = Join-Path $unpacked ($payload.BaseName)
    if (-not (Test-Path -LiteralPath $tarFile)) { throw "Inner tar archive was not found." }
    & $sevenZip.Source x -y "-o$target" $tarFile | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to extract SpeexDSP files." }

    if (-not (Test-Path -LiteralPath $dll)) { throw "speexdsp.dll was not found after extraction." }
    Write-Output "SpeexDSP installed: $dll"
    Write-Output "PVAI_AEC_LIBRARY=models/speexdsp/Library/bin/speexdsp.dll"
}
finally {
    if (Test-Path -LiteralPath $stageRoot) {
        Remove-Item -LiteralPath $stageRoot -Recurse -Force
    }
}
