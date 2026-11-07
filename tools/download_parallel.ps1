param(
    [Parameter(Mandatory = $true)][string]$Url,
    [Parameter(Mandatory = $true)][string]$Output,
    [int]$Connections = 10,
    [long]$PartSizeMB = 64,
    [int]$Retries = 8
)

$ErrorActionPreference = "Stop"

function Get-RemoteLength {
    param([string]$Link)
    $headers = & curl.exe -sIL --max-time 30 $Link
    $values = @()
    foreach ($line in $headers) {
        if ($line -match "^content-length:\s*(\d+)") {
            $values += [int64]$Matches[1]
        }
    }
    if (-not $values) { throw "No content-length found for $Link" }
    return ($values | Select-Object -Last 1)
}

$total = Get-RemoteLength -Link $Url
$workDir = $Output + ".parts"
New-Item -ItemType Directory -Force -Path $workDir | Out-Null

$partSize = [int64]($PartSizeMB * 1MB)
$partCount = [int][math]::Ceiling($total / $partSize)
Write-Host ("Total: {0} MB | parts: {1} x {2} MB | concurrency: {3}" -f [math]::Round($total / 1MB, 1), $partCount, $PartSizeMB, $Connections)

for ($attempt = 1; $attempt -le $Retries; $attempt++) {
    $missing = @()
    for ($index = 0; $index -lt $partCount; $index++) {
        $start = [int64]$index * $partSize
        $expected = [int64][math]::Min($partSize, $total - $start)
        if ($expected -le 0) { break }
        $partFile = Join-Path $workDir ("part{0:d4}.bin" -f $index)
        $ok = $false
        if (Test-Path $partFile) {
            $ok = ((Get-Item $partFile).Length -eq $expected)
            if (-not $ok) { Remove-Item $partFile -Force -ErrorAction SilentlyContinue }
        }
        if (-not $ok) { $missing += @($index, $start, $expected, $partFile) }
    }

    if (-not $missing) { break }
    $missingCount = $missing.Count / 4
    Write-Host ("Round {0}: downloading {1} missing part(s)..." -f $attempt, $missingCount)

    $jobs = New-Object System.Collections.Generic.List[object]
    for ($position = 0; $position -lt $missing.Count; $position += 4) {
        $index = [int]$missing[$position]
        $start = [int64]$missing[$position + 1]
        $byteCount = [int64]$missing[$position + 2]
        $partFile = [string]$missing[$position + 3]
        $range = "$start-" + ($start + $byteCount - 1)
        while ($jobs.Count -ge $Connections) {
            Start-Sleep -Milliseconds 500
            $alive = New-Object System.Collections.Generic.List[object]
            foreach ($job in $jobs) {
                $job.Refresh()
                if (-not $job.HasExited) { $alive.Add([object]$job) }
            }
            $jobs = $alive
        }
        $argumentList = @('-sSL', '--fail', '--show-error', '-r', $range, '-o', ('"' + $partFile + '"'), $Url)
        $process = Start-Process -FilePath "curl.exe" -ArgumentList $argumentList -WindowStyle Hidden -PassThru -ErrorAction Continue
        $null = $jobs.Add([object]$process)
        Write-Host ("  launched part {0:d4} ({1} MB, pid {2})" -f $index, [math]::Round($byteCount / 1MB, 1), $process.Id)
    }
    foreach ($job in $jobs) {
        if (-not $job.HasExited) {
            try { $job.WaitForExit(120000) | Out-Null } catch { }
            if (-not $job.HasExited) { Stop-Process -Id $job.Id -Force -ErrorAction SilentlyContinue }
        }
    }
    $stillMissing = 0
    for ($index = 0; $index -lt $partCount; $index++) {
        $start = [int64]$index * $partSize
        $expected = [int64][math]::Min($partSize, $total - $start)
        if ($expected -le 0) { break }
        $partFile = Join-Path $workDir ("part{0:d4}.bin" -f $index)
        if ((Test-Path $partFile) -and ((Get-Item $partFile).Length -eq $expected)) { continue }
        $stillMissing++
    }
    if ($stillMissing -and $attempt -lt $Retries) {
        Write-Host ("Round {0}: {1} part(s) still incomplete, retrying..." -f $attempt, $stillMissing)
        Start-Sleep -Seconds 5
    }
}

foreach ($index in 0..($partCount - 1)) {
    $start = [int64]$index * $partSize
    $expected = [int64][math]::Min($partSize, $total - $start)
    if ($expected -le 0) { break }
    $partFile = Join-Path $workDir ("part{0:d4}.bin" -f $index)
    if ((Test-Path $partFile) -and ((Get-Item $partFile).Length -eq $expected)) { continue }
    Write-Host "FAILED: part $index is missing or corrupt after all retries."
    exit 1
}

$streams = @()
try {
    $target = [System.IO.File]::Create($Output)
    for ($index = 0; $index -lt $partCount; $index++) {
        $partFile = Join-Path $workDir ("part{0:d4}.bin" -f $index)
        $stream = [System.IO.File]::OpenRead($partFile)
        $null = $streams.Add($stream)
    }
    foreach ($stream in $streams) {
        $stream.CopyTo($target)
    }
    $target.Close()
} finally {
    foreach ($stream in $streams) { if ($stream) { $stream.Close() } }
}

$final = (Get-Item $Output).Length
if ($final -ne $total) {
    Write-Host ("FAILED: assembled size {0} != expected {1}" -f $final, $total)
    exit 1
}
Remove-Item $workDir -Recurse -Force -ErrorAction SilentlyContinue
Write-Host ("SUCCESS: {0} assembled ({1} MB)" -f $Output, [math]::Round($final / 1MB, 1))
exit 0
