param([string]$Agent)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot "backend\.runtime\windows\python\python.exe"
$env:PYTHONPATH = $null
$env:PYTHONNOUSERSITE = "1"
if (-not (Test-Path -LiteralPath $python)) { throw "Offline Python is missing. Run scripts\bootstrap_offline.ps1." }
$arguments = @("-m", "agent_eval.offline_doctor", "--project-root", (Join-Path $projectRoot "backend"))
if ($Agent) { $arguments += @("--agent", $Agent) }
& $python @arguments
exit $LASTEXITCODE
