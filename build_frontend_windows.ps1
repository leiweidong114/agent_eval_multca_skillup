[CmdletBinding()]
param([switch]$CleanInstall)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\windows\common.ps1')

$projectRoot = Get-AgentEvalProjectRoot -StartPath $PSScriptRoot
Import-AgentEvalEnv -ProjectRoot $projectRoot | Out-Null
$npm = Get-AgentEvalConfiguredPath -ProjectRoot $projectRoot -Name 'NPM_EXECUTABLE' -Default 'backend/.runtime/windows/node/npm.cmd'
$cache = Join-Path $projectRoot 'backend\.offline-cache\windows\npm-cache'
$frontend = Join-Path $projectRoot 'frontend'

if (-not (Test-Path -LiteralPath $npm)) { throw "npm was not found: $npm" }
if ($CleanInstall -or -not (Test-Path -LiteralPath (Join-Path $frontend 'node_modules'))) {
    if (-not (Test-Path -LiteralPath $cache)) { throw "Offline npm cache was not found: $cache" }
    Invoke-AgentEvalCommand -FilePath $npm -WorkingDirectory $frontend -ArgumentList @(
        'ci', '--offline', '--cache', $cache
    )
}
Invoke-AgentEvalCommand -FilePath $npm -WorkingDirectory $frontend -ArgumentList @('run', 'build')
Write-Host 'FRONTEND_BUILD_OK' -ForegroundColor Green
