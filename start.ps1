[CmdletBinding()]
param([switch]$OpenBrowser)

& (Join-Path $PSScriptRoot 'start-all.ps1') -OpenBrowser:$OpenBrowser
