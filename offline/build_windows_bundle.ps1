param(
    [string]$OutputDirectory = "",
    [string]$CacheDirectory = "",
    [switch]$SkipTests,
    [switch]$KeepStage
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repoRoot "dist-offline" }
if (-not $CacheDirectory) { $CacheDirectory = Join-Path $repoRoot ".offline-cache" }
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
$CacheDirectory = [System.IO.Path]::GetFullPath($CacheDirectory)
$lock = Get-Content -Raw (Join-Path $PSScriptRoot "runtime-lock.json") | ConvertFrom-Json
$branch = (git -C $repoRoot branch --show-current).Trim()
$commit = (git -C $repoRoot rev-parse HEAD).Trim()
if ((git -C $repoRoot status --porcelain)) {
    throw "Commit or stash repository changes before building the offline package."
}

New-Item -ItemType Directory -Force -Path $OutputDirectory,$CacheDirectory | Out-Null
$stage = Join-Path $OutputDirectory (".stage-" + [guid]::NewGuid().ToString("N"))
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory).TrimEnd('\')
$resolvedStage = [System.IO.Path]::GetFullPath($stage)
if (-not $resolvedStage.StartsWith($resolvedOutput + '\', [System.StringComparison]::OrdinalIgnoreCase) -or
    -not ([System.IO.Path]::GetFileName($resolvedStage)).StartsWith('.stage-')) {
    throw "Unsafe staging path: $resolvedStage"
}
$packageName = "agent-eval-full-offline-win-x64-$($commit.Substring(0,8))"
$packageRoot = Join-Path $stage $packageName
$bundle = Join-Path $stage "agent_eval_multca_skillup.bundle"
$pythonBuildRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("agent-eval-python-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $stage | Out-Null

function Get-Asset([object]$Entry, [string]$Subdirectory) {
    $cacheFile = Join-Path $CacheDirectory $Entry.file
    if (-not (Test-Path -LiteralPath $cacheFile)) {
        Write-Host "Downloading $($Entry.url)"
        $partial = "$cacheFile.partial"
        if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force }
        for ($attempt = 1; $attempt -le 3; $attempt++) {
            try {
                Invoke-WebRequest -Uri $Entry.url -OutFile $partial
                Move-Item -LiteralPath $partial -Destination $cacheFile -Force
                break
            } catch {
                if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force }
                if ($attempt -eq 3) { throw }
                Start-Sleep -Seconds (2 * $attempt)
            }
        }
    }
    $destination = Join-Path $packageRoot "offline\assets\$Subdirectory"
    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    Copy-Item -LiteralPath $cacheFile -Destination (Join-Path $destination $Entry.file) -Force
    return $cacheFile
}

try {
    git -C $repoRoot bundle create $bundle --all
    if ($LASTEXITCODE -ne 0) { throw "Failed to create Git bundle." }
    git clone --branch $branch $bundle $packageRoot
    if ($LASTEXITCODE -ne 0) { throw "Failed to create standalone package repository." }
    git -C $packageRoot remote remove origin
    New-Item -ItemType Directory -Force -Path (Join-Path $packageRoot "offline\assets\git") | Out-Null
    Copy-Item $bundle (Join-Path $packageRoot "offline\assets\git\agent_eval_multca_skillup.bundle")

    $pythonInstaller = Get-Asset $lock.python "python"
    $goArchive = Get-Asset $lock.go "go"
    $nodeArchive = Get-Asset $lock.node "node"
    $null = Get-Asset $lock.git "git"
    $null = Get-Asset $lock.vc_redist "system"

    $builderRoot = Join-Path $stage "builder"
    New-Item -ItemType Directory -Force -Path $pythonBuildRoot | Out-Null
    $pythonInstallLog = Join-Path $pythonBuildRoot "install.log"
    $pythonInstall = Start-Process -FilePath $pythonInstaller -Wait -PassThru -WindowStyle Hidden -ArgumentList @(
        "/quiet", "InstallAllUsers=0", "Include_launcher=0", "Include_test=0",
        "Include_pip=1", "PrependPath=0", "Shortcuts=0",
        "TargetDir=`"$pythonBuildRoot`"", "/log", "`"$pythonInstallLog`""
    )
    $python = Join-Path $pythonBuildRoot "python.exe"
    if ($pythonInstall.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $python)) {
        throw "Build Python installation failed with exit code $($pythonInstall.ExitCode); see $pythonInstallLog."
    }
    & $python -m pip install --upgrade pip setuptools wheel
    & $python -m pip install -r (Join-Path $packageRoot "offline\python-requirements.in")
    if ($LASTEXITCODE -ne 0) { throw "Python dependency resolution failed." }
    $pythonAssetRoot = Join-Path $packageRoot "offline\assets\python"
    $requirementsLock = Join-Path $pythonAssetRoot "requirements.lock"
    & $python -m pip freeze --exclude-editable | Where-Object { $_ -notmatch '^agent-eval-' } | Set-Content -Encoding utf8 $requirementsLock
    & $python -m pip download --only-binary=:all: --dest (Join-Path $pythonAssetRoot "wheelhouse") -r $requirementsLock
    & $python -m pip download --only-binary=:all: --dest (Join-Path $pythonAssetRoot "wheelhouse") pip setuptools wheel
    if ($LASTEXITCODE -ne 0) { throw "Python wheelhouse creation failed." }
    $pythonRuntimeArchive = Join-Path $pythonAssetRoot "python-runtime.zip"
    Compress-Archive -Path (Join-Path $pythonBuildRoot "*") -DestinationPath $pythonRuntimeArchive -CompressionLevel Optimal
    $packagePython = Join-Path $packageRoot "backend\.runtime\windows\python"
    New-Item -ItemType Directory -Force -Path $packagePython | Out-Null
    Copy-Item (Join-Path $pythonBuildRoot "*") $packagePython -Recurse -Force

    $goBuildRoot = Join-Path $builderRoot "go"
    Expand-Archive -LiteralPath $goArchive -DestinationPath $goBuildRoot -Force
    $go = Join-Path $goBuildRoot "go\bin\go.exe"
    $env:GOTOOLCHAIN = "local"
    $env:CGO_ENABLED = "0"
    $sources = Join-Path $packageRoot "offline\assets\sources"
    New-Item -ItemType Directory -Force -Path $sources | Out-Null
    git clone --depth 1 --branch $lock.multica.tag $lock.multica.repository (Join-Path $sources "multica")
    git clone --depth 1 --branch $lock.skill_up.tag $lock.skill_up.repository (Join-Path $sources "skill-up")
    $actualMultica = (git -C (Join-Path $sources "multica") rev-parse HEAD).Trim()
    $actualSkillUp = (git -C (Join-Path $sources "skill-up") rev-parse HEAD).Trim()
    if ($actualMultica -ne $lock.multica.commit) { throw "Unexpected Multica commit: $actualMultica" }
    if ($actualSkillUp -ne $lock.skill_up.commit) { throw "Unexpected Skill-Up commit: $actualSkillUp" }
    git -C (Join-Path $sources "skill-up") apply (Join-Path $packageRoot "backend\patches\skill-up-v0.9.1-windows-custom-engine.patch")
    if ($LASTEXITCODE -ne 0) { throw "Skill-Up patch failed." }
    Push-Location (Join-Path $sources "multica\server")
    try { & $go mod vendor } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "Multica vendoring failed." }
    Push-Location (Join-Path $sources "skill-up")
    try { & $go mod vendor } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "Skill-Up vendoring failed." }

    $nodeBuildRoot = Join-Path $builderRoot "node"
    Expand-Archive -LiteralPath $nodeArchive -DestinationPath $nodeBuildRoot -Force
    $node = Get-ChildItem $nodeBuildRoot -Filter node.exe -Recurse -File | Select-Object -First 1
    $npm = Join-Path $node.Directory.FullName "npm.cmd"
    $npmCache = Join-Path $packageRoot "offline\assets\npm-cache"
    $oldPath = $env:PATH
    $env:PATH = "$($node.Directory.FullName);$oldPath"
    Push-Location (Join-Path $packageRoot "frontend")
    try {
        & $npm ci --cache $npmCache --prefer-online --no-audit
        if ($LASTEXITCODE -ne 0) { throw "npm cache population failed." }
        & $npm run build
        if ($LASTEXITCODE -ne 0) { throw "Initial frontend build failed." }
    } finally {
        Pop-Location
        $env:PATH = $oldPath
    }

    & (Join-Path $packageRoot "scripts\bootstrap_offline.ps1") -SkipTests:$SkipTests
    if ($LASTEXITCODE -ne 0) { throw "Packaged runtime bootstrap failed." }

    $manifestFiles = Get-ChildItem (Join-Path $packageRoot "offline\assets") -Recurse -File | ForEach-Object {
        [pscustomobject]@{
            path = $_.FullName.Substring($packageRoot.Length + 1).Replace("\", "/")
            bytes = $_.Length
            sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
        }
    }
    $manifest = [ordered]@{
        schema_version = 1
        package = $packageName
        git_commit = $commit
        git_branch = $branch
        built_at_utc = [DateTime]::UtcNow.ToString("o")
        runtime_lock = $lock
        files = @($manifestFiles)
    }
    $manifest | ConvertTo-Json -Depth 10 | Set-Content -Encoding utf8 (Join-Path $packageRoot "offline-manifest.json")
    $zip = Join-Path $OutputDirectory "$packageName.zip"
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
    Compress-Archive -Path $packageRoot -DestinationPath $zip -CompressionLevel Optimal
    $zipHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLowerInvariant()
    "$zipHash  $([IO.Path]::GetFileName($zip))" | Set-Content -Encoding ascii "$zip.sha256"
    Write-Host "OFFLINE_PACKAGE_OK $zip"
} finally {
    if (-not $KeepStage -and (Test-Path -LiteralPath $stage)) {
        Remove-Item -LiteralPath $stage -Recurse -Force
    } elseif ($KeepStage) {
        Write-Host "Stage retained: $stage"
    }
    $resolvedTemp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\')
    $resolvedPythonBuild = [System.IO.Path]::GetFullPath($pythonBuildRoot)
    if ($resolvedPythonBuild.StartsWith($resolvedTemp + '\', [System.StringComparison]::OrdinalIgnoreCase) -and
        ([System.IO.Path]::GetFileName($resolvedPythonBuild)).StartsWith('agent-eval-python-') -and
        (Test-Path -LiteralPath $pythonBuildRoot)) {
        Remove-Item -LiteralPath $pythonBuildRoot -Recurse -Force
    }
}
