[CmdletBinding()]
param([switch]$Test)

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
& (Join-Path $repositoryRoot 'build_skillup_windows.ps1') -Test:$Test
