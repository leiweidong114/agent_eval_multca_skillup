param([switch]$SkipTests)

$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "rebuild_backend.ps1") -SkipTests:$SkipTests
& (Join-Path $PSScriptRoot "rebuild_go.ps1") -SkipTests:$SkipTests
& (Join-Path $PSScriptRoot "rebuild_frontend.ps1")
& (Join-Path $PSScriptRoot "rebuild_skill_tools.ps1")
Write-Host "OFFLINE_REBUILD_ALL_OK"
