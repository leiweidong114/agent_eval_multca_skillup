param([string]$HostAddress = "127.0.0.1", [int]$Port = 8000)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot "backend\.runtime\windows\python\python.exe"
$env:PYTHONPATH = $null
$env:PYTHONNOUSERSITE = "1"
$toolchains = Join-Path $projectRoot ".runtime\toolchains"
$node = Get-ChildItem (Join-Path $toolchains "node") -Filter node.exe -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1
$runtimePaths = @(
    (Join-Path $projectRoot "backend\.runtime\windows\python"),
    (Join-Path $projectRoot "backend\.runtime\windows\python\Scripts"),
    (Join-Path $toolchains "go\go\bin"),
    $(if ($node) { $node.Directory.FullName }),
    (Join-Path $toolchains "git\cmd")
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
$env:PATH = ($runtimePaths -join ";") + ";" + $env:PATH
$env:NODE_PATH = Join-Path $projectRoot "offline\skill-node-tools\node_modules"
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $projectRoot "offline\assets\playwright-browsers"
if (-not (Test-Path -LiteralPath $python)) { throw "Offline Python is missing. Run scripts\bootstrap_offline.ps1." }
Write-Host "Agent Eval: http://$HostAddress`:$Port"
& $python (Join-Path $projectRoot "backend\run_server.py") --host $HostAddress --port $Port
