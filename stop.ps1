[CmdletBinding()]
param([switch]$Quiet)

& (Join-Path $PSScriptRoot 'stop-all.ps1') -Quiet:$Quiet
