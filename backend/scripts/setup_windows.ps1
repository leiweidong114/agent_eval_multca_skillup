param([switch]$SkipTests)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Split-Path -Parent $PSScriptRoot)).Path
Set-Location $projectRoot
$runtime = Join-Path $projectRoot ".runtime\windows"
$binDir = Join-Path $runtime "bin"
$goHome = Join-Path $runtime "go"
$go = Join-Path $goHome "bin\go.exe"
$pythonEnv = Join-Path $runtime "python"
$python = Join-Path $pythonEnv "Scripts\python.exe"
$multicaSource = Join-Path $runtime "src\multica"
$multicaCommit = "c1a61e1e863eb62ddd7b5fd5ab5ff85391f212fd"
New-Item -ItemType Directory -Force -Path $runtime, $binDir | Out-Null

if (-not (Test-Path -LiteralPath $go)) {
    $goVersion = "1.26.7"
    $architecture = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "amd64" }
    $checksums = @{
        amd64 = "f4f534a486e4bc3387fa18f08208f2f854b7aaea8a08f2a2d829a914a05abb11"
        arm64 = "6f1b08de9e2dd94f69c52e524ab6834737275253291e8fd7f1c12ed4eceeda89"
    }
    $archiveName = "go$goVersion.windows-$architecture.zip"
    $archive = Join-Path $runtime $archiveName
    if (-not (Test-Path -LiteralPath $archive)) {
        Invoke-WebRequest -Uri "https://go.dev/dl/$archiveName" -OutFile $archive
    }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
    if ($actual -ne $checksums[$architecture]) { throw "Go checksum mismatch: $actual" }
    Expand-Archive -LiteralPath $archive -DestinationPath $runtime -Force
}

if (-not (Test-Path -LiteralPath $multicaSource)) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $multicaSource) | Out-Null
    git clone --depth 1 --branch v0.4.36 https://github.com/multica-ai/multica.git $multicaSource
    if ($LASTEXITCODE -ne 0) { throw "Failed to download Multica source" }
}
$actualCommit = (git -C $multicaSource rev-parse HEAD).Trim()
if ($actualCommit -ne $multicaCommit) {
    throw "Unexpected Multica source commit: $actualCommit"
}
$commandDir = Join-Path $multicaSource "server\cmd\multica-eval-runtime"
New-Item -ItemType Directory -Force -Path $commandDir | Out-Null
Copy-Item runtime\multica-local-runner\main.go,runtime\multica-local-runner\main_test.go -Destination $commandDir -Force
Push-Location (Join-Path $multicaSource "server")
try {
    & $go build -trimpath -o (Join-Path $binDir "multica-eval-runtime.exe") .\cmd\multica-eval-runtime
    if ($LASTEXITCODE -ne 0) { throw "Multica local runtime build failed" }
    if (-not $SkipTests) {
        & $go test .\cmd\multica-eval-runtime
        if ($LASTEXITCODE -ne 0) { throw "Multica local runtime tests failed" }
    }
} finally {
    Pop-Location
}

# Always verify the pinned source and rebuild the Windows custom-engine patch.
& .\scripts\install_skillup_windows.ps1
$bootstrapPython = Get-Command python -ErrorAction Stop
if (-not (Test-Path -LiteralPath $python)) {
    & $bootstrapPython.Source -m venv --copies $pythonEnv
}
& $python -m pip install -e ".[dev]"
if (-not $SkipTests) {
    & $python -m pytest
    if ($LASTEXITCODE -ne 0) { throw "Python tests failed" }
}
& $python -m agent_eval.cli doctor
Write-Host "WINDOWS_SETUP_OK"
