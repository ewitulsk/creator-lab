# Start the creator-lab server in the background (http://localhost:5050) and remember its PID.
# Logs: <DATA_ROOT>\logs\server.log (plus server.stdout.log / server.stderr.log next to it).
#   powershell -File scripts\serve.ps1            # from the repo root
#   powershell -File scripts\stop.ps1             # stops it again (the whole process tree)
param(
    [int]$Port = 5050,
    [string]$LogDir = "D:\VideoAnalysis\data\logs"
)
$ErrorActionPreference = "Stop"
$backend = Join-Path $PSScriptRoot "..\backend" | Resolve-Path
$pidFile = Join-Path $LogDir "server.pid"
New-Item -ItemType Directory -Force $LogDir | Out-Null

$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    Write-Error "Port $Port is already in use (PID $($listening.OwningProcess -join ',')). Run scripts\stop.ps1 first."
}

$uv = (Get-Command uv).Source
$p = Start-Process -FilePath $uv -ArgumentList "run", "creator-lab", "serve", "--host", "127.0.0.1", "--port", "$Port" `
    -WorkingDirectory $backend -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $LogDir "server.stdout.log") `
    -RedirectStandardError (Join-Path $LogDir "server.stderr.log")
Set-Content $pidFile $p.Id
Write-Host "creator-lab starting on http://localhost:$Port (PID $($p.Id), logs in $LogDir)"
