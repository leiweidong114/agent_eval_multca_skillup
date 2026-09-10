[CmdletBinding()]
param(
    [ValidateSet('All', 'Backend', 'Frontend', 'SkillUp', 'Multica')]
    [string]$Target = 'All',
    [switch]$Test,
    [switch]$Restart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\windows\common.ps1')
$projectRoot = Get-AgentEvalProjectRoot -StartPath $PSScriptRoot
Import-AgentEvalEnv -ProjectRoot $projectRoot | Out-Null
if ($Restart) { & (Join-Path $projectRoot 'stop.ps1') -Quiet }

if ($Target -in @('All', 'Backend')) {
    $python = Get-AgentEvalConfiguredPath -ProjectRoot $projectRoot -Name 'PYTHON_EXECUTABLE' -Default 'backend/.runtime/windows/python/Scripts/python.exe'
    $wheelhouse = Join-Path $projectRoot 'backend\.offline-cache\windows\wheelhouse'
    if (-not (Test-Path -LiteralPath $python)) { throw "Python was not found: $python" }
    if (-not (Test-Path -LiteralPath $wheelhouse)) { throw "Python wheelhouse was not found: $wheelhouse" }
    Invoke-AgentEvalCommand -FilePath $python -ArgumentList @(
        '-m', 'pip', 'install', '--no-index', '--find-links', $wheelhouse,
        '-e', "$((Join-Path $projectRoot 'backend'))[dev,web,database]"
    )
    if ($Test) { Invoke-AgentEvalCommand -FilePath $python -WorkingDirectory (Join-Path $projectRoot 'backend') -ArgumentList @('-m', 'pytest') }
    Write-Host 'BACKEND_BUILD_OK' -ForegroundColor Green
}
if ($Target -in @('All', 'Frontend')) { & (Join-Path $projectRoot 'build_frontend_windows.ps1') -CleanInstall:$false }
if ($Target -in @('All', 'SkillUp')) { & (Join-Path $projectRoot 'build_skillup_windows.ps1') -Test:$Test }
if ($Target -in @('All', 'Multica')) { & (Join-Path $projectRoot 'build_multica_windows.ps1') -Test:$Test }
if ($Restart) { & (Join-Path $projectRoot 'start.ps1') }
Write-Host "REBUILD_OK: $Target" -ForegroundColor Green
