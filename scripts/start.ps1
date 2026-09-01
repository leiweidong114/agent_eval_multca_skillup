param([string]$HostAddress = "127.0.0.1", [int]$Port = 8000)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot "backend\.runtime\windows\python\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Offline Python is missing. Run scripts\bootstrap_offline.ps1." }
Write-Host "Agent Eval: http://$HostAddress`:$Port"
& $python (Join-Path $projectRoot "backend\run_server.py") --host $HostAddress --port $Port
