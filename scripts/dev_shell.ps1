$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$toolchains = Join-Path $projectRoot ".runtime\toolchains"
$env:PYTHONPATH = $null
$env:PYTHONNOUSERSITE = "1"
$paths = @(
    (Join-Path $projectRoot "backend\.runtime\windows\python"),
    (Join-Path $projectRoot "backend\.runtime\windows\python\Scripts"),
    (Join-Path $toolchains "go\go\bin"),
    ((Get-ChildItem (Join-Path $toolchains "node") -Filter node.exe -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1).Directory.FullName),
    (Join-Path $toolchains "git\cmd")
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
$env:PATH = ($paths -join ";") + ";" + $env:PATH
Set-Location $projectRoot
Write-Host "Offline development shell ready at $projectRoot"
Write-Host "Python: $(& python --version 2>&1)"
Write-Host "Node: $(& node --version 2>&1)"
Write-Host "Go: $(& go version 2>&1)"
Write-Host "Git: $(& git --version 2>&1)"
