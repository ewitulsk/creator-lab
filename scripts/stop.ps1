# Stop the creator-lab server started by scripts\serve.ps1: the whole process tree
# (uv launches creator-lab.exe, which runs python; killing only uv leaves the server up).
param(
    [int]$Port = 5050,
    [string]$LogDir = "D:\VideoAnalysis\data\logs"
)
$pidFile = Join-Path $LogDir "server.pid"
if (Test-Path $pidFile) {
    $serverPid = Get-Content $pidFile
    taskkill /T /F /PID $serverPid 2>$null | Out-Null
    Remove-Item $pidFile -ErrorAction SilentlyContinue
}
# Anything still listening on the port (e.g. a server started some other way).
Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { taskkill /T /F /PID $_.OwningProcess 2>$null | Out-Null }
Start-Sleep -Milliseconds 500
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Write-Error "Something is still listening on port $Port."
} else {
    Write-Host "creator-lab stopped."
}
