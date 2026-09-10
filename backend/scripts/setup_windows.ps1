[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ReleaseRoot,
    [switch]$SkipTests,
    [switch]$SkipJustDo,
    [switch]$SkipFrontend
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Write-Warning 'backend/scripts/setup_windows.ps1 is a compatibility wrapper; prefer root install_windows.ps1.'
& (Join-Path $repositoryRoot 'install_windows.ps1') `
    -ReleaseRoot $ReleaseRoot `
    -ProjectRoot $repositoryRoot `
    -SkipJustDo:$SkipJustDo `
    -SkipFrontend:$SkipFrontend `
    -SkipVerify:$SkipTests
