$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Split-Path -Parent $PSScriptRoot)).Path
$go = Join-Path $projectRoot ".runtime\windows\go\bin\go.exe"
$source = Join-Path $projectRoot ".runtime\windows\src\skill-up"
$binary = Join-Path $projectRoot ".tools\windows\skill-up.exe"
$patch = Join-Path $projectRoot "patches\skill-up-v0.9.1-windows-custom-engine.patch"
$expectedCommit = "80c3147101f81017c66f882b767bdc532de5e74f"

if (-not (Test-Path -LiteralPath $go)) {
    throw "Portable Go is missing. Run scripts/setup_windows.ps1 first."
}
if (-not (Test-Path -LiteralPath (Join-Path $source ".git"))) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $source) | Out-Null
    git clone --depth 1 --branch v0.9.1 https://github.com/alibaba/skill-up.git $source
    if ($LASTEXITCODE -ne 0) { throw "Failed to download Skill-Up source" }
}
$actualCommit = (git -C $source rev-parse HEAD).Trim()
if ($actualCommit -ne $expectedCommit) {
    throw "Unexpected Skill-Up source commit: $actualCommit"
}

git -C $source apply --check $patch 2>$null
if ($LASTEXITCODE -eq 0) {
    git -C $source apply $patch
    if ($LASTEXITCODE -ne 0) { throw "Failed to apply Skill-Up Windows patch" }
} else {
    git -C $source apply --reverse --check $patch 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Skill-Up source is neither clean nor already patched"
    }
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $binary) | Out-Null
Push-Location $source
try {
    & $go build -trimpath -o $binary .\cmd\skill-up
    if ($LASTEXITCODE -ne 0) { throw "Skill-Up build failed" }
} finally {
    Pop-Location
}
Write-Host "SKILL_UP_WINDOWS_BUILD_OK"
