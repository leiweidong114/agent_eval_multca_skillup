param([switch]$SkipTests)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backend = Join-Path $projectRoot "backend"
$python = Join-Path $backend ".runtime\windows\python\python.exe"
$wheelhouse = Join-Path $projectRoot "offline\assets\python\wheelhouse"
$lock = Join-Path $projectRoot "offline\assets\python\requirements.lock"

if (-not (Test-Path -LiteralPath $python)) { throw "Offline Python is missing. Run scripts\bootstrap_offline.ps1." }
if (-not (Test-Path -LiteralPath $lock)) { throw "Python lock file is missing from the offline assets." }

& $python -m pip install --no-index --find-links $wheelhouse -r $lock
if ($LASTEXITCODE -ne 0) { throw "Offline Python dependency installation failed." }
& $python -m pip install --no-index --no-deps --no-build-isolation --force-reinstall $backend
if ($LASTEXITCODE -ne 0) { throw "Agent Eval backend installation failed." }
if (-not $SkipTests) {
    Push-Location $backend
    try { & $python -m pytest } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "Backend tests failed." }
}
Write-Host "BACKEND_REBUILD_OK"
