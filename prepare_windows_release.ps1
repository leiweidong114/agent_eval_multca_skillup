[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$OutputRoot,
    [Parameter(Mandatory)][string]$GoArchive,
    [Parameter(Mandatory)][string]$NodeArchive,
    [Parameter(Mandatory)][Alias('PythonInstaller')][string]$PythonPackage,
    [string]$VCRedist,
    [Parameter(Mandatory)][string]$Wheelhouse,
    [Parameter(Mandatory)][string]$NpmCache,
    [Parameter(Mandatory)][string]$QuestionBank,
    [string]$JustDoInstaller = 'D:\AI_FOR_WORLD\14_AI_workspace\common_tools\JustDo\release\JustDo Setup 2026.8.27.exe',
    [string]$SkillUpSource,
    [string]$MulticaSource,
    [string]$ZipPath,
    [switch]$SkipVendor
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\windows\common.ps1')
$projectRoot = Get-AgentEvalProjectRoot -StartPath $PSScriptRoot
Import-AgentEvalEnv -ProjectRoot $projectRoot | Out-Null

if (-not $SkillUpSource) { $SkillUpSource = Join-Path $projectRoot 'backend\.runtime\windows\src\skill-up' }
if (-not $MulticaSource) { $MulticaSource = Join-Path $projectRoot 'backend\.runtime\windows\src\multica' }
$requiredFiles = @($GoArchive, $NodeArchive, $PythonPackage, $QuestionBank, $JustDoInstaller)
foreach ($path in $requiredFiles) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required file was not found: $path" }
}
foreach ($path in @($Wheelhouse, $NpmCache, $SkillUpSource, $MulticaSource)) {
    if (-not (Test-Path -LiteralPath $path -PathType Container)) { throw "Required directory was not found: $path" }
}

$output = [System.IO.Path]::GetFullPath($OutputRoot)
if (Test-Path -LiteralPath $output) {
    $existing = @(Get-ChildItem -LiteralPath $output -Force)
    if ($existing.Count -gt 0) { throw "OutputRoot must be empty or absent: $output" }
} else {
    New-Item -ItemType Directory -Force -Path $output | Out-Null
}

$toolchains = Join-Path $output 'toolchains'
$releaseWheelhouse = Join-Path $output 'python\wheelhouse'
$releaseNpmCache = Join-Path $output 'frontend\npm-cache'
$releaseSources = Join-Path $output 'sources'
$justDo = Join-Path $output 'justdo'
$questionBankRoot = Join-Path $output 'question-bank'
New-Item -ItemType Directory -Force -Path $toolchains, $releaseWheelhouse, $releaseNpmCache, $releaseSources, $justDo, $questionBankRoot | Out-Null

Copy-Item -LiteralPath $GoArchive, $NodeArchive, $PythonPackage -Destination $toolchains
if ($VCRedist) {
    if (-not (Test-Path -LiteralPath $VCRedist -PathType Leaf)) { throw "VC Runtime installer was not found: $VCRedist" }
    Copy-Item -LiteralPath $VCRedist -Destination (Join-Path $toolchains 'VC_redist.x64.exe')
}
Copy-AgentEvalDirectoryContents -Source $Wheelhouse -Destination $releaseWheelhouse
Copy-AgentEvalDirectoryContents -Source $NpmCache -Destination $releaseNpmCache
function Copy-ReleaseSource {
    param([string]$Source, [string]$Destination)
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    & robocopy.exe $Source $Destination /E /COPY:DAT /DCOPY:DAT /R:2 /W:1 /NFL /NDL /NJH /NJS /NP /XD .git node_modules dist | Out-Null
    $robocopyExitCode = $LASTEXITCODE
    if ($robocopyExitCode -ge 8) {
        throw "Source copy failed with robocopy exit code $robocopyExitCode`: $Source -> $Destination"
    }
}
Copy-ReleaseSource -Source $SkillUpSource -Destination (Join-Path $releaseSources 'skill-up')
Copy-ReleaseSource -Source $MulticaSource -Destination (Join-Path $releaseSources 'multica')
Copy-Item -LiteralPath $JustDoInstaller -Destination $justDo
Copy-Item -LiteralPath $QuestionBank -Destination (Join-Path $questionBankRoot 'maeval-public.db')

if (-not $SkipVendor) {
    $go = Get-AgentEvalConfiguredPath -ProjectRoot $projectRoot -Name 'GO_EXECUTABLE' -Default 'backend/.runtime/windows/go/bin/go.exe'
    if (-not (Test-Path -LiteralPath $go)) { throw "Go was not found: $go" }
    Invoke-AgentEvalCommand -FilePath $go -WorkingDirectory (Join-Path $releaseSources 'skill-up') -ArgumentList @('mod', 'vendor')
    Invoke-AgentEvalCommand -FilePath $go -WorkingDirectory (Join-Path $releaseSources 'multica\server') -ArgumentList @('mod', 'vendor')
}

if ($ZipPath) {
    $zip = [System.IO.Path]::GetFullPath($ZipPath)
    if (Test-Path -LiteralPath $zip) { throw "ZipPath already exists: $zip" }
    Compress-Archive -Path (Join-Path $output '*') -DestinationPath $zip -CompressionLevel Optimal
    Write-Host "RELEASE_ZIP_READY: $zip" -ForegroundColor Green
}
Write-Host "RELEASE_DIRECTORY_READY: $output" -ForegroundColor Green
