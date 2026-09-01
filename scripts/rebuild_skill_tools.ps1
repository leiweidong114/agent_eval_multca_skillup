$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$toolRoot = Join-Path $projectRoot "offline\skill-node-tools"
$node = Get-ChildItem (Join-Path $projectRoot ".runtime\toolchains\node") -Filter node.exe -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $node) { throw "Offline Node.js is missing. Run scripts\bootstrap_offline.ps1." }
$npm = Join-Path $node.Directory.FullName "npm.cmd"
$npmCache = Join-Path $projectRoot "offline\assets\npm-cache"
$oldPath = $env:PATH
$env:PATH = "$($node.Directory.FullName);$oldPath"
Push-Location $toolRoot
try {
    & $npm ci --offline --cache $npmCache --prefer-offline --no-audit
    if ($LASTEXITCODE -ne 0) { throw "Offline Skill Node dependency installation failed." }
} finally {
    Pop-Location
    $env:PATH = $oldPath
}
Write-Host "SKILL_NODE_TOOLS_REBUILD_OK"
