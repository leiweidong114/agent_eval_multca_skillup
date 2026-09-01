param([switch]$SkipTests)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backend = Join-Path $projectRoot "backend"
$go = Get-ChildItem (Join-Path $projectRoot ".runtime\toolchains\go") -Filter go.exe -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $go) { throw "Offline Go is missing. Run scripts\bootstrap_offline.ps1." }
$multicaArchive = Join-Path $projectRoot "offline\assets\sources\multica-v0.4.36-vendored.tar.gz"
$skillUpArchive = Join-Path $projectRoot "offline\assets\sources\skill-up-v0.9.1-patched-vendored.tar.gz"
if (-not (Test-Path -LiteralPath $multicaArchive)) { throw "Vendored Multica source archive is missing." }
if (-not (Test-Path -LiteralPath $skillUpArchive)) { throw "Vendored Skill-Up source archive is missing." }
$buildRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("agent-eval-go-build-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null
$multica = Join-Path $buildRoot "multica"
$skillUp = Join-Path $buildRoot "skill-up"

try {
    & tar.exe -xzf $multicaArchive -C $buildRoot
    if ($LASTEXITCODE -ne 0) { throw "Multica source extraction failed." }
    & tar.exe -xzf $skillUpArchive -C $buildRoot
    if ($LASTEXITCODE -ne 0) { throw "Skill-Up source extraction failed." }
    if (-not (Test-Path -LiteralPath (Join-Path $multica "server\vendor"))) { throw "Vendored Multica source is incomplete." }
    if (-not (Test-Path -LiteralPath (Join-Path $skillUp "vendor"))) { throw "Vendored Skill-Up source is incomplete." }
    $env:CGO_ENABLED = "0"
    $env:GOTOOLCHAIN = "local"
    $commandDir = Join-Path $multica "server\cmd\multica-eval-runtime"
    New-Item -ItemType Directory -Force -Path $commandDir | Out-Null
    Copy-Item (Join-Path $backend "runtime\multica-local-runner\main.go"),(Join-Path $backend "runtime\multica-local-runner\main_test.go") -Destination $commandDir -Force
    $runtimeOut = Join-Path $backend ".runtime\windows\bin\multica-eval-runtime.exe"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $runtimeOut) | Out-Null
    Push-Location (Join-Path $multica "server")
    try {
        & $go.FullName build -mod=vendor -trimpath -o $runtimeOut .\cmd\multica-eval-runtime
        if ($LASTEXITCODE -ne 0) { throw "Multica runtime build failed." }
        if (-not $SkipTests) {
            & $go.FullName test -mod=vendor .\cmd\multica-eval-runtime
            if ($LASTEXITCODE -ne 0) { throw "Multica runtime tests failed." }
        }
    } finally { Pop-Location }
    $skillOut = Join-Path $backend ".tools\windows\skill-up.exe"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $skillOut) | Out-Null
    Push-Location $skillUp
    try {
        & $go.FullName build -mod=vendor -trimpath -o $skillOut .\cmd\skill-up
        if ($LASTEXITCODE -ne 0) { throw "Skill-Up build failed." }
    } finally { Pop-Location }
} finally {
    $resolvedTemp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\')
    $resolvedBuild = [System.IO.Path]::GetFullPath($buildRoot)
    if ($resolvedBuild.StartsWith($resolvedTemp + '\', [System.StringComparison]::OrdinalIgnoreCase) -and
        ([System.IO.Path]::GetFileName($resolvedBuild)).StartsWith('agent-eval-go-build-') -and
        (Test-Path -LiteralPath $buildRoot)) {
        Remove-Item -LiteralPath $buildRoot -Recurse -Force
    }
}
Write-Host "GO_REBUILD_OK"
