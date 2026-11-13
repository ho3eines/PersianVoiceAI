#requires -Version 5.1
<#
    Persian Voice AI - PC-0 hardware inventory

    Collects: OS, CPU, RAM, every GPU with its VRAM and the source of that
    number, physical disks, volumes and free space. Uses nvidia-smi when it is
    available and keeps the WMI value otherwise.

    Deliberately does NOT collect disk serial numbers, the user name, IP or
    MAC addresses, and never calls an external service.
#>
[CmdletBinding()]
param(
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
$scriptVersion = "1.0"
$schema = "persianvoiceai.hardware/1"

function Convert-ToGb {
    param([object]$Bytes)
    if ($null -eq $Bytes) { return 0 }
    $value = 0.0
    try { $value = [double]$Bytes } catch { return 0 }
    if ($value -le 0) { return 0 }
    return [math]::Round($value / 1GB, 2)
}

if (-not $OutputDirectory -or $OutputDirectory.Trim() -eq "") {
    if ($PSScriptRoot) { $OutputDirectory = $PSScriptRoot }
    else { $OutputDirectory = (Get-Location).Path }
}
if (-not (Test-Path -LiteralPath $OutputDirectory)) {
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
}

$now = Get-Date
$stamp = $now.ToString("yyyyMMdd_HHmmss")

# ---------------------------------------------------------------------- OS
$os = Get-CimInstance -ClassName Win32_OperatingSystem
$cpu = Get-CimInstance -ClassName Win32_Processor | Select-Object -First 1

# --------------------------------------------------------------------- RAM
$modules = @()
try {
    $modules = @(Get-CimInstance -ClassName Win32_PhysicalMemory | ForEach-Object {
            [pscustomobject]@{
                capacity_gb = Convert-ToGb $_.Capacity
                speed_mhz   = [int]$_.Speed
            }
        })
} catch {
    $modules = @()
}

$memoryTotal = [double]$os.TotalVisibleMemorySize * 1KB
$memoryFree = [double]$os.FreePhysicalMemory * 1KB

# --------------------------------------------------------------------- GPU
$gpus = @()
foreach ($vc in @(Get-CimInstance -ClassName Win32_VideoController)) {
    $adapterBytes = 0.0
    if ($null -ne $vc.AdapterRAM) {
        try { $adapterBytes = [double]$vc.AdapterRAM } catch { $adapterBytes = 0.0 }
    }
    $driverDate = ""
    if ($vc.DriverDate) {
        try { $driverDate = ([datetime]$vc.DriverDate).ToString("yyyy-MM-dd") } catch { $driverDate = "" }
    }
    $gpus += [pscustomobject]@{
        name              = [string]$vc.Name
        driver_version    = [string]$vc.DriverVersion
        driver_date       = $driverDate
        adapter_ram_bytes = [int64]$adapterBytes
        adapter_ram_gb    = Convert-ToGb $adapterBytes
        vram_source       = "WMI Win32_VideoController.AdapterRAM"
        video_processor   = [string]$vc.VideoProcessor
        status            = [string]$vc.Status
    }
}

# nvidia-smi is optional; its absence does not mean there is no NVIDIA card.
$nvidiaSmiPath = ""
$nvidiaSmiStatus = "not_found"
$nvidiaGpus = @()
$candidates = @()
$nvidiaCommand = Get-Command -Name nvidia-smi -ErrorAction SilentlyContinue
if ($nvidiaCommand) { $candidates += $nvidiaCommand.Source }
if ($env:SystemRoot) { $candidates += (Join-Path $env:SystemRoot "System32\nvidia-smi.exe") }
$candidates += "C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"

foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) {
        $nvidiaSmiPath = $candidate
        break
    }
}

if ($nvidiaSmiPath) {
    try {
        $smiLines = @(& $nvidiaSmiPath --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits 2>$null)
        if ($LASTEXITCODE -eq 0 -and $smiLines.Count -gt 0) {
            foreach ($line in $smiLines) {
                $parts = ([string]$line) -split "," | ForEach-Object { $_.Trim() }
                if ($parts.Count -lt 3) { continue }
                $memoryMb = 0.0
                try { $memoryMb = [double]$parts[1] } catch { $memoryMb = 0.0 }
                $nvidiaGpus += [pscustomobject]@{
                    name           = [string]$parts[0]
                    vram_mb        = [int]$memoryMb
                    vram_gb        = [math]::Round($memoryMb / 1024, 2)
                    driver_version = [string]$parts[2]
                }
            }
            if ($nvidiaGpus.Count -gt 0) { $nvidiaSmiStatus = "ok" }
            else { $nvidiaSmiStatus = "no_output" }
        } else {
            $nvidiaSmiStatus = "failed"
        }
    } catch {
        $nvidiaSmiStatus = "failed"
    }
}

# Prefer nvidia-smi numbers when the names match.
if ($nvidiaGpus.Count -gt 0) {
    foreach ($gpu in $gpus) {
        foreach ($nv in $nvidiaGpus) {
            if (-not $gpu.name -or -not $nv.name) { continue }
            $left = $gpu.name.ToLower()
            $right = $nv.name.ToLower()
            if ($left.Contains($right) -or $right.Contains($left)) {
                $gpu.adapter_ram_gb = $nv.vram_gb
                $gpu.adapter_ram_bytes = [int64]($nv.vram_mb * 1MB)
                $gpu.vram_source = "nvidia-smi memory.total (MiB)"
                $gpu.driver_version = $nv.driver_version
            }
        }
    }
}

# ------------------------------------------------------------------- disks
$physicalDisks = @()
$physicalDiskSource = "Get-PhysicalDisk"
try {
    $physicalDisks = @(Get-PhysicalDisk | ForEach-Object {
            [pscustomobject]@{
                model      = [string]$_.FriendlyName
                media_type = [string]$_.MediaType
                bus_type   = [string]$_.BusType
                size_gb    = Convert-ToGb $_.Size
                health     = [string]$_.HealthStatus
            }
        })
} catch {
    $physicalDiskSource = "Win32_DiskDrive"
    $physicalDisks = @(Get-CimInstance -ClassName Win32_DiskDrive | ForEach-Object {
            [pscustomobject]@{
                model      = [string]$_.Model
                media_type = ""
                bus_type   = [string]$_.InterfaceType
                size_gb    = Convert-ToGb $_.Size
                health     = [string]$_.Status
            }
        })
}

$volumes = @()
$volumeSource = "Get-Volume"
try {
    $volumes = @(Get-Volume | Where-Object { $_.DriveLetter } | ForEach-Object {
            $size = 0.0
            $free = 0.0
            try { $size = [double]$_.Size } catch { $size = 0.0 }
            try { $free = [double]$_.SizeRemaining } catch { $free = 0.0 }
            $percent = 0
            if ($size -gt 0) { $percent = [math]::Round(($free / $size) * 100, 1) }
            [pscustomobject]@{
                drive_letter = [string]$_.DriveLetter
                label        = [string]$_.FileSystemLabel
                file_system  = [string]$_.FileSystem
                size_gb      = Convert-ToGb $size
                free_gb      = Convert-ToGb $free
                free_percent = $percent
            }
        })
} catch {
    $volumeSource = "Win32_LogicalDisk"
    $volumes = @(Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DriveType=3" | ForEach-Object {
            $size = 0.0
            $free = 0.0
            try { $size = [double]$_.Size } catch { $size = 0.0 }
            try { $free = [double]$_.FreeSpace } catch { $free = 0.0 }
            $percent = 0
            if ($size -gt 0) { $percent = [math]::Round(($free / $size) * 100, 1) }
            [pscustomobject]@{
                drive_letter = [string]$_.DeviceID
                label        = [string]$_.VolumeName
                file_system  = [string]$_.FileSystem
                size_gb      = Convert-ToGb $size
                free_gb      = Convert-ToGb $free
                free_percent = $percent
            }
        })
}

# Drive that holds this report: the one to check before downloading models.
$outputDrive = ""
try {
    $outputDrive = ([System.IO.Path]::GetPathRoot($OutputDirectory)).TrimEnd("\").TrimEnd(":")
} catch {
    $outputDrive = ""
}
$selectedDrive = $null
if ($outputDrive) {
    $selectedDrive = $volumes | Where-Object { $_.drive_letter -eq $outputDrive } | Select-Object -First 1
}

$warnings = @()
if ($selectedDrive -and $selectedDrive.free_gb -lt 10) {
    $warnings += ("Low free space on drive {0}: {1} GB. Check before downloading any model." -f $selectedDrive.drive_letter, $selectedDrive.free_gb)
}
if ($nvidiaSmiStatus -ne "ok") {
    $warnings += ("nvidia-smi not usable (status: {0}); VRAM comes from WMI and may be inaccurate." -f $nvidiaSmiStatus)
}
foreach ($gpu in $gpus) {
    if ($gpu.name -match "Intel" -and $gpu.adapter_ram_gb -gt 0) {
        $warnings += ("{0} uses shared system memory; its reported AdapterRAM is not dedicated VRAM." -f $gpu.name)
    }
}

# ------------------------------------------------------------------ report
$report = [pscustomobject]@{
    schema               = $schema
    script_version       = $scriptVersion
    collected_at_utc     = $now.ToUniversalTime().ToString("o")
    collected_at_local   = $now.ToString("yyyy-MM-dd HH:mm:ss")
    os                   = [pscustomobject]@{
        caption = [string]$os.Caption
        version = [string]$os.Version
        build   = [string]$os.BuildNumber
        arch    = [string]$os.OSArchitecture
    }
    cpu                  = [pscustomobject]@{
        name          = ([string]$cpu.Name).Trim()
        cores         = [int]$cpu.NumberOfCores
        threads       = [int]$cpu.NumberOfLogicalProcessors
        max_clock_mhz = [int]$cpu.MaxClockSpeed
        l3_cache_kb   = [int]$cpu.L3CacheSize
    }
    memory               = [pscustomobject]@{
        total_gb = Convert-ToGb $memoryTotal
        free_gb  = Convert-ToGb $memoryFree
        modules  = $modules
    }
    gpus                 = $gpus
    nvidia_smi           = [pscustomobject]@{
        status = $nvidiaSmiStatus
        path   = $nvidiaSmiPath
        gpus   = $nvidiaGpus
    }
    physical_disks       = $physicalDisks
    physical_disk_source = $physicalDiskSource
    volumes              = $volumes
    volume_source        = $volumeSource
    selected_drive       = $selectedDrive
    warnings             = $warnings
    privacy              = [pscustomobject]@{
        collects_serial_numbers  = $false
        collects_username        = $false
        collects_ip_or_mac       = $false
        calls_external_services  = $false
    }
}

$jsonPath = Join-Path $OutputDirectory ("PersianVoiceAI_Hardware_" + $stamp + ".json")
$txtPath = Join-Path $OutputDirectory ("PersianVoiceAI_Hardware_" + $stamp + ".txt")

$json = $report | ConvertTo-Json -Depth 8
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($jsonPath, $json + [Environment]::NewLine, $utf8)

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("Persian Voice AI - PC-0 hardware inventory")
$lines.Add("Collected: " + [string]$report.collected_at_local + "  (UTC " + [string]$report.collected_at_utc + ")")
$lines.Add("Schema: " + $schema + " | script " + $scriptVersion)
$lines.Add("")
$lines.Add("[OS]    " + [string]$os.Caption + " | version " + [string]$os.Version + " | build " + [string]$os.BuildNumber + " | " + [string]$os.OSArchitecture)
$lines.Add("[CPU]   " + ([string]$cpu.Name).Trim() + " | cores=" + [string]$cpu.NumberOfCores + " threads=" + [string]$cpu.NumberOfLogicalProcessors + " | max " + [string]$cpu.MaxClockSpeed + " MHz | L3 " + [string]$cpu.L3CacheSize + " KB")
$lines.Add("[RAM]   total " + [string]$report.memory.total_gb + " GB | free " + [string]$report.memory.free_gb + " GB")
foreach ($module in $modules) {
    $lines.Add("        module " + [string]$module.capacity_gb + " GB @ " + [string]$module.speed_mhz + " MHz")
}
$lines.Add("")
$lines.Add("[GPU]   count=" + [string]$gpus.Count)
foreach ($gpu in $gpus) {
    $lines.Add("        - " + [string]$gpu.name + " | driver " + [string]$gpu.driver_version + " (" + [string]$gpu.driver_date + ") | VRAM " + [string]$gpu.adapter_ram_gb + " GB | source: " + [string]$gpu.vram_source)
}
$lines.Add("        nvidia-smi: " + $nvidiaSmiStatus)
if ($nvidiaSmiPath) { $lines.Add("        nvidia-smi path: " + $nvidiaSmiPath) }
$lines.Add("")
$lines.Add("[DISKS] source=" + $physicalDiskSource)
foreach ($disk in $physicalDisks) {
    $lines.Add("        - " + [string]$disk.model + " | " + [string]$disk.media_type + " | " + [string]$disk.bus_type + " | " + [string]$disk.size_gb + " GB | " + [string]$disk.health)
}
$lines.Add("")
$lines.Add("[VOLUMES] source=" + $volumeSource)
foreach ($volume in $volumes) {
    $lines.Add("        " + [string]$volume.drive_letter + " " + [string]$volume.label + " | " + [string]$volume.file_system + " | total " + [string]$volume.size_gb + " GB | free " + [string]$volume.free_gb + " GB (" + [string]$volume.free_percent + "%)")
}
$lines.Add("")
if ($selectedDrive) {
    $lines.Add("[TARGET] " + [string]$selectedDrive.drive_letter + " has " + [string]$selectedDrive.free_gb + " GB free - check this before downloading any model.")
}
if ($warnings.Count -gt 0) {
    $lines.Add("")
    $lines.Add("[WARNINGS]")
    foreach ($warning in $warnings) { $lines.Add("        - " + [string]$warning) }
}
$lines.Add("")
$lines.Add("Privacy: no serial numbers, user name, IP or MAC addresses are collected; no external service is called.")

[System.IO.File]::WriteAllText($txtPath, ($lines -join [Environment]::NewLine) + [Environment]::NewLine, $utf8)

Write-Output ("JSON: " + $jsonPath)
Write-Output ("TXT : " + $txtPath)
Write-Output ("CPU : " + ([string]$cpu.Name).Trim() + " (cores=" + [string]$cpu.NumberOfCores + ", threads=" + [string]$cpu.NumberOfLogicalProcessors + ")")
Write-Output ("RAM : " + [string]$report.memory.total_gb + " GB total, " + [string]$report.memory.free_gb + " GB free")
foreach ($gpu in $gpus) {
    Write-Output ("GPU : " + [string]$gpu.name + " - VRAM " + [string]$gpu.adapter_ram_gb + " GB (" + [string]$gpu.vram_source + ")")
}
if ($selectedDrive) {
    Write-Output ("DISK: " + [string]$selectedDrive.drive_letter + " free " + [string]$selectedDrive.free_gb + " GB of " + [string]$selectedDrive.size_gb + " GB")
}
