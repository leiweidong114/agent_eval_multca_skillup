param([switch]$SkipInstall)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$frontend = Join-Path $projectRoot "frontend"
$node = Get-ChildItem (Join-Path $projectRoot ".runtime\toolchains\node") -Filter node.exe -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $node) { throw "Offline Node.js is missing. Run scripts\bootstrap_offline.ps1." }
$nodeDir = $node.Directory.FullName
$npm = Join-Path $nodeDir "npm.cmd"
$npmCache = Join-Path $projectRoot "offline\assets\npm-cache"
$oldPath = $env:PATH
$env:PATH = "$nodeDir;$oldPath"
Push-Location $frontend
try {
    if (-not $SkipInstall) {
        & $npm ci --offline --cache $npmCache --prefer-offline --no-audit
        if ($LASTEXITCODE -ne 0) { throw "Offline npm install failed." }
    }
    & $npm run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }
} finally {
    Pop-Location
    $env:PATH = $oldPath
}
Write-Host "FRONTEND_REBUILD_OK"
