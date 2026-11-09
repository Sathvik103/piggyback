# Start Docker Desktop on Windows and bring up Piggyback's database.
#
#   powershell -ExecutionPolicy Bypass -File scripts\start_docker.ps1
#
# On some Windows builds Docker Desktop leaves Unix-socket files behind when it
# stops (e.g. on reboot), can't delete or replace them on the next start, and
# crashes with "rename ...sailor-ingest.sock ... The file cannot be accessed by
# the system". Those files can't be deleted either, but their folders can be
# renamed, so this moves the two socket folders aside before starting Docker.
# The *.stale-* folders it leaves can be deleted after a reboot.

$ErrorActionPreference = "Stop"

function Test-Engine {
    docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
}

if (-not (Test-Engine)) {
    Get-Process -Name "Docker Desktop", "com.docker.backend", "com.docker.build" -ErrorAction SilentlyContinue |
        Stop-Process -Force -Confirm:$false
    Start-Sleep -Seconds 2
    wsl --shutdown
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    foreach ($dir in @("$env:LOCALAPPDATA\Docker\run", "$env:LOCALAPPDATA\docker-secrets-engine")) {
        if (Test-Path $dir) {
            Rename-Item $dir "$(Split-Path $dir -Leaf).stale-$stamp"
            Write-Host "moved aside $dir"
        }
    }
    Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    $deadline = (Get-Date).AddMinutes(5)
    while (-not (Test-Engine)) {
        if ((Get-Date) -gt $deadline) {
            throw "Docker engine did not start; see $env:LOCALAPPDATA\Docker\log\host\com.docker.backend.exe.log"
        }
        Start-Sleep -Seconds 5
    }
}
Write-Host "Docker engine is up"

Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    docker compose up -d --wait db
} finally {
    Pop-Location
}
