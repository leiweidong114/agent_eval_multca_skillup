[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$OutputRoot,
    [Parameter(Mandatory)][string]$GoArchive,
    [Parameter(Mandatory)][string]$NodeArchive,
    [Parameter(Mandatory)][string]$PythonInstaller,
    [Parameter(Mandatory)][string]$Wheelhouse,
    [Parameter(Mandatory)][string]$NpmCache,
    [Parameter(Mandatory)][string]$QuestionBank,
    [string]$JustDoInstaller = 'D:\AI_FOR_WORLD\14_AI_workspace\common_tools\JustDo\release\JustDo Setup 2026.8.27.exe',
    [string]$SkillUpSource,
    [string]$MulticaSource,
    [string]$SkillUpBinary,
    [string]$MulticaBinary,
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
if (-not $SkillUpBinary) { $SkillUpBinary = Join-Path $projectRoot 'backend\.tools\windows\skill-up.exe' }
if (-not $MulticaBinary) { $MulticaBinary = Join-Path $projectRoot 'backend\.runtime\windows\bin\multica-eval-runtime.exe' }

$requiredFiles = @($GoArchive, $NodeArchive, $PythonInstaller, $QuestionBank, $JustDoInstaller, $SkillUpBinary, $MulticaBinary)
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
$prebuilt = Join-Path $output 'prebuilt'
$justDo = Join-Path $output 'justdo'
$questionBankRoot = Join-Path $output 'question-bank'
New-Item -ItemType Directory -Force -Path $toolchains, $releaseWheelhouse, $releaseNpmCache, $releaseSources, $prebuilt, $justDo, $questionBankRoot | Out-Null

Copy-Item -LiteralPath $GoArchive, $NodeArchive, $PythonInstaller -Destination $toolchains
Copy-AgentEvalDirectoryContents -Source $Wheelhouse -Destination $releaseWheelhouse
Copy-AgentEvalDirectoryContents -Source $NpmCache -Destination $releaseNpmCache
function Copy-ReleaseSource {
    param([string]$Source, [string]$Destination)
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Get-ChildItem -LiteralPath $Source -Force | Where-Object {
        $_.Name -notin @('.git', 'node_modules', 'dist')
    } | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $Destination -Recurse -Force
    }
}
Copy-ReleaseSource -Source $SkillUpSource -Destination (Join-Path $releaseSources 'skill-up')
Copy-ReleaseSource -Source $MulticaSource -Destination (Join-Path $releaseSources 'multica')
Copy-Item -LiteralPath $SkillUpBinary -Destination (Join-Path $prebuilt 'skill-up.exe')
Copy-Item -LiteralPath $MulticaBinary -Destination (Join-Path $prebuilt 'multica-eval-runtime.exe')
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
