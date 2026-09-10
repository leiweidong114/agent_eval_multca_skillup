[CmdletBinding()]
param([switch]$OpenBrowser)

$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'stop.ps1') -Quiet
& (Join-Path $PSScriptRoot 'start.ps1') -OpenBrowser:$OpenBrowser
