$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$botMain = Join-Path $scriptDir "main.py"
$botMainForMatch = ($botMain -replace "\\", "/")
$outLog = Join-Path $scriptDir "runner.out.log"
$errLog = Join-Path $scriptDir "runner.err.log"

if (-not (Test-Path $botMain)) {
    throw "Bot file not found: $botMain"
}

$existing = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -like "python*" -and ($_.CommandLine -like "*$botMainForMatch*" -or $_.CommandLine -like "*$botMain*")
}

if ($existing) {
    Write-Output "Bot already running"
    exit 0
}

Start-Process -FilePath "py" `
    -ArgumentList "-3.14", $botMain `
    -WorkingDirectory $scriptDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog

Write-Output "Bot started by guard"
