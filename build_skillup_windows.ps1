[CmdletBinding()]
param([switch]$Test)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\windows\common.ps1')

$projectRoot = Get-AgentEvalProjectRoot -StartPath $PSScriptRoot
Import-AgentEvalEnv -ProjectRoot $projectRoot | Out-Null
$go = Get-AgentEvalConfiguredPath -ProjectRoot $projectRoot -Name 'GO_EXECUTABLE' -Default 'backend/.runtime/windows/go/bin/go.exe'
$source = Join-Path $projectRoot 'backend\.runtime\windows\src\skill-up'
$binary = Get-AgentEvalConfiguredPath -ProjectRoot $projectRoot -Name 'SKILLUP_EXECUTABLE' -Default 'backend/.tools/windows/skill-up.exe'
$patch = Join-Path $projectRoot 'backend\patches\skill-up-v0.9.1-windows-custom-engine.patch'

if (-not (Test-Path -LiteralPath $go)) { throw "Go was not found: $go" }
if (-not (Test-Path -LiteralPath (Join-Path $source 'go.mod'))) { throw "Skill-Up source was not found: $source" }
if (-not (Test-Path -LiteralPath (Join-Path $source 'vendor'))) {
    throw 'Skill-Up vendor directory is missing. The offline release must include vendored Go dependencies.'
}

if (Test-Path -LiteralPath $patch) {
    Push-Location $source
    try {
        & git apply --check --ignore-space-change --ignore-whitespace $patch 2>$null
        if ($LASTEXITCODE -eq 0) {
            & git apply --ignore-space-change --ignore-whitespace $patch
            if ($LASTEXITCODE -ne 0) { throw 'Failed to apply the Skill-Up Windows patch.' }
        } else {
            & git apply --reverse --check --ignore-space-change --ignore-whitespace $patch 2>$null
            if ($LASTEXITCODE -ne 0) {
                throw 'Skill-Up source is neither clean nor already patched.'
            }
        }
    }
    finally { Pop-Location }
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $binary) | Out-Null
$temporaryBinary = "$binary.new"
Invoke-WithAgentEvalOfflineGo {
    Invoke-AgentEvalCommand -FilePath $go -WorkingDirectory $source -ArgumentList @(
        'build', '-mod=vendor', '-trimpath', '-o', $temporaryBinary, '.\cmd\skill-up'
    )
    if ($Test) {
        Invoke-AgentEvalCommand -FilePath $go -WorkingDirectory $source -ArgumentList @('test', '-mod=vendor', '.\...')
    }
}
Move-Item -LiteralPath $temporaryBinary -Destination $binary -Force
Write-Host "SKILL_UP_BUILD_OK: $binary" -ForegroundColor Green
