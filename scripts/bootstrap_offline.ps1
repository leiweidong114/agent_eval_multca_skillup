param([switch]$Force, [switch]$SkipTests)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$assets = Join-Path $projectRoot "offline\assets"
$toolchains = Join-Path $projectRoot ".runtime\toolchains"
$backendPython = Join-Path $projectRoot "backend\.runtime\windows\python"
if (-not (Test-Path -LiteralPath $assets)) { throw "offline\assets is missing; this is not a complete offline package." }

$manifestPath = Join-Path $projectRoot "offline-manifest.json"
if (Test-Path -LiteralPath $manifestPath) {
    $manifest = Get-Content -Raw $manifestPath | ConvertFrom-Json
    foreach ($item in $manifest.files) {
        $target = Join-Path $projectRoot ($item.path.Replace('/', '\'))
        if (-not (Test-Path -LiteralPath $target)) { throw "Offline asset is missing: $($item.path)" }
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $target).Hash.ToLowerInvariant()
        if ($actual -ne $item.sha256) { throw "Offline asset checksum mismatch: $($item.path)" }
    }
    Write-Host "OFFLINE_ASSET_CHECKSUMS_OK"
}

function Expand-Toolchain([string]$Archive, [string]$Destination, [string]$Marker) {
    if ($Force -and (Test-Path -LiteralPath $Destination)) { Remove-Item -LiteralPath $Destination -Recurse -Force }
    if (-not (Test-Path -LiteralPath (Join-Path $Destination $Marker))) {
        New-Item -ItemType Directory -Force -Path $Destination | Out-Null
        Expand-Archive -LiteralPath $Archive -DestinationPath $Destination -Force
    }
}

New-Item -ItemType Directory -Force -Path $toolchains | Out-Null
$goArchive = Get-ChildItem (Join-Path $assets "go") -Filter "go*.zip" -File | Select-Object -First 1
$nodeArchive = Get-ChildItem (Join-Path $assets "node") -Filter "node*.zip" -File | Select-Object -First 1
if (-not $goArchive -or -not $nodeArchive) { throw "Go or Node.js archive is missing." }
Expand-Toolchain $goArchive.FullName (Join-Path $toolchains "go") "go\bin\go.exe"
Expand-Toolchain $nodeArchive.FullName (Join-Path $toolchains "node") $nodeArchive.BaseName

$portableGit = Get-ChildItem (Join-Path $assets "git") -Filter "PortableGit*.exe" -File | Select-Object -First 1
$gitTarget = Join-Path $toolchains "git"
if ($portableGit -and ($Force -or -not (Test-Path -LiteralPath (Join-Path $gitTarget "cmd\git.exe")))) {
    New-Item -ItemType Directory -Force -Path $gitTarget | Out-Null
    & $portableGit.FullName -y -o"$gitTarget" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "PortableGit extraction failed." }
}
$gitMetadata = Join-Path $assets "git\repository-metadata.tar.gz"
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".git"))) {
    if (-not (Test-Path -LiteralPath $gitMetadata)) { throw "Standalone Git metadata archive is missing." }
    & tar.exe -xzf $gitMetadata -C $projectRoot
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $projectRoot ".git"))) {
        throw "Standalone Git repository restoration failed."
    }
    Write-Host "OFFLINE_GIT_REPOSITORY_RESTORED"
}

$pythonArchive = Join-Path $assets "python\python-runtime.zip"
if (-not (Test-Path -LiteralPath $pythonArchive)) { throw "Offline Python runtime archive is missing." }
if ($Force -and (Test-Path -LiteralPath $backendPython)) { Remove-Item -LiteralPath $backendPython -Recurse -Force }
if (-not (Test-Path -LiteralPath (Join-Path $backendPython "python.exe"))) {
    New-Item -ItemType Directory -Force -Path $backendPython | Out-Null
    Expand-Archive -LiteralPath $pythonArchive -DestinationPath $backendPython -Force
}

& (Join-Path $PSScriptRoot "rebuild_all.ps1") -SkipTests:$SkipTests
$localConfig = Join-Path $projectRoot "backend\config\local.yaml"
if (-not (Test-Path -LiteralPath $localConfig)) {
    Copy-Item (Join-Path $projectRoot "backend\config\local.example.yaml") $localConfig
    Write-Warning "Created backend\config\local.yaml. Set the intranet server, model and credentials before running doctor."
}
Write-Host "OFFLINE_BOOTSTRAP_OK"
