[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ReleaseRoot,
    [string]$ProjectRoot = $PSScriptRoot,
    [switch]$Force,
    [switch]$SkipJustDo,
    [switch]$InteractiveJustDo,
    [switch]$InstallVCRuntime,
    [switch]$SkipFrontend,
    [switch]$SkipVerify
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\windows\common.ps1')

$ProjectRoot = Get-AgentEvalProjectRoot -StartPath $ProjectRoot
$ReleaseRoot = (Resolve-Path -LiteralPath $ReleaseRoot).Path
$runtime = Join-Path $ProjectRoot 'backend\.runtime\windows'
$tools = Join-Path $ProjectRoot 'backend\.tools\windows'
$cache = Join-Path $ProjectRoot 'backend\.offline-cache\windows'
$sourceRoot = Join-Path $runtime 'src'
$binRoot = Join-Path $runtime 'bin'

if (-not [Environment]::Is64BitOperatingSystem) {
    throw 'Only 64-bit Windows is supported by this offline bundle.'
}
New-Item -ItemType Directory -Force -Path $runtime, $tools, $cache, $sourceRoot, $binRoot | Out-Null

$envFile = Join-Path $ProjectRoot '.env'
if (-not (Test-Path -LiteralPath $envFile)) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot '.env.example') -Destination $envFile
    Write-Host 'Created .env from .env.example. Add private credentials after installation.' -ForegroundColor Yellow
}
Import-AgentEvalEnv -ProjectRoot $ProjectRoot | Out-Null

function Install-ZipToolchain {
    param([string]$Name, [string]$Archive, [string]$Target, [string]$ExecutableRelativePath)
    $executable = Join-Path $Target $ExecutableRelativePath
    if ((Test-Path -LiteralPath $executable) -and -not $Force) {
        Write-Host "$Name already installed: $Target"
        return
    }
    if (Test-Path -LiteralPath $Target) {
        if (-not $Force) { throw "$Name target exists but is incomplete: $Target" }
        Assert-AgentEvalChildPath -Parent $runtime -Child $Target | Out-Null
        Remove-Item -LiteralPath $Target -Recurse -Force
    }
    $staging = Join-Path $runtime ('.install-' + $Name.ToLowerInvariant())
    Assert-AgentEvalChildPath -Parent $runtime -Child $staging | Out-Null
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    Invoke-AgentEvalCommand -FilePath 'tar.exe' -ArgumentList @('-xf', $Archive, '-C', $staging)
    $roots = @(Get-ChildItem -LiteralPath $staging -Directory)
    $contentRoot = if ($roots.Count -eq 1) { $roots[0].FullName } else { $staging }
    Copy-AgentEvalDirectoryContents -Source $contentRoot -Destination $Target
    Remove-Item -LiteralPath $staging -Recurse -Force
    if (-not (Test-Path -LiteralPath $executable)) { throw "$Name executable was not installed: $executable" }
}

$goArchive = Find-AgentEvalAsset -ReleaseRoot $ReleaseRoot -Patterns @(
    'toolchains\go*.windows-amd64.zip', 'toolchains\go\*.zip', 'go*.windows-amd64.zip'
)
$nodeArchive = Find-AgentEvalAsset -ReleaseRoot $ReleaseRoot -Patterns @(
    'toolchains\node*-win-x64.zip', 'toolchains\node\*.zip', 'node*-win-x64.zip'
)
$pythonPortable = Find-AgentEvalAsset -ReleaseRoot $ReleaseRoot -Optional -Patterns @(
    'toolchains\cpython*-install_only*.tar.gz', 'toolchains\python*.tar.gz', 'python*.tar.gz'
)
$pythonInstaller = Find-AgentEvalAsset -ReleaseRoot $ReleaseRoot -Optional -Patterns @(
    'toolchains\python*-amd64.exe', 'toolchains\python\*.exe', 'python*-amd64.exe'
)
if (-not $pythonPortable -and -not $pythonInstaller) {
    throw 'A portable CPython .tar.gz or Python amd64 installer was not found in the release input.'
}
$vcRedist = Find-AgentEvalAsset -ReleaseRoot $ReleaseRoot -Optional -Patterns @(
    'toolchains\VC_redist.x64.exe', 'VC_redist.x64.exe'
)

if ($vcRedist -and $InstallVCRuntime) {
    $vcProcess = Start-Process -FilePath $vcRedist -ArgumentList @('/install', '/quiet', '/norestart') -Wait -PassThru
    if ($vcProcess.ExitCode -notin @(0, 1638, 3010)) {
        throw "Visual C++ Runtime installation failed with exit code $($vcProcess.ExitCode)."
    }
}

Install-ZipToolchain -Name 'Go' -Archive $goArchive -Target (Join-Path $runtime 'go') -ExecutableRelativePath 'bin\go.exe'
Install-ZipToolchain -Name 'Node' -Archive $nodeArchive -Target (Join-Path $runtime 'node') -ExecutableRelativePath 'node.exe'

$pythonBase = Join-Path $runtime 'python-base'
$basePython = Join-Path $pythonBase 'python.exe'
if (-not (Test-Path -LiteralPath $basePython) -or $Force) {
    if ((Test-Path -LiteralPath $pythonBase) -and $Force) {
        Assert-AgentEvalChildPath -Parent $runtime -Child $pythonBase | Out-Null
        Remove-Item -LiteralPath $pythonBase -Recurse -Force
    }
    if ($pythonPortable) {
        $pythonStaging = Join-Path $runtime '.install-python'
        Assert-AgentEvalChildPath -Parent $runtime -Child $pythonStaging | Out-Null
        if (Test-Path -LiteralPath $pythonStaging) { Remove-Item -LiteralPath $pythonStaging -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $pythonStaging | Out-Null
        Invoke-AgentEvalCommand -FilePath 'tar.exe' -ArgumentList @('-xzf', $pythonPortable, '-C', $pythonStaging)
        $pythonContent = if (Test-Path -LiteralPath (Join-Path $pythonStaging 'python\python.exe')) {
            Join-Path $pythonStaging 'python'
        } else { $pythonStaging }
        Copy-AgentEvalDirectoryContents -Source $pythonContent -Destination $pythonBase
        Remove-Item -LiteralPath $pythonStaging -Recurse -Force
    } else {
        New-Item -ItemType Directory -Force -Path $pythonBase | Out-Null
        $pythonArguments = @(
            '/quiet', 'InstallAllUsers=0', "TargetDir=$pythonBase", 'Include_launcher=0',
            'PrependPath=0', 'Include_test=0', 'Include_pip=1', 'Include_tcltk=0'
        )
        $process = Start-Process -FilePath $pythonInstaller -ArgumentList $pythonArguments -Wait -PassThru
        if ($process.ExitCode -ne 0) { throw "Python installation failed with exit code $($process.ExitCode)." }
    }
    if (-not (Test-Path -LiteralPath $basePython)) {
        throw "Python executable was not installed: $basePython"
    }
}

$pythonEnv = Join-Path $runtime 'python'
$python = Join-Path $pythonEnv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python) -or $Force) {
    if ((Test-Path -LiteralPath $pythonEnv) -and $Force) {
        Assert-AgentEvalChildPath -Parent $runtime -Child $pythonEnv | Out-Null
        Remove-Item -LiteralPath $pythonEnv -Recurse -Force
    }
    Invoke-WithAgentEvalPythonIsolation {
        Invoke-AgentEvalCommand -FilePath $basePython -ArgumentList @('-m', 'venv', '--copies', $pythonEnv)
    }
}

$wheelhouse = Find-AgentEvalAsset -ReleaseRoot $ReleaseRoot -Directory -Patterns @(
    'python\wheelhouse', 'wheelhouse'
)
$projectWheelhouse = Join-Path $cache 'wheelhouse'
New-Item -ItemType Directory -Force -Path $projectWheelhouse | Out-Null
Copy-AgentEvalDirectoryContents -Source $wheelhouse -Destination $projectWheelhouse
Invoke-WithAgentEvalPythonIsolation {
    Invoke-AgentEvalCommand -FilePath $python -ArgumentList @(
        '-m', 'pip', 'install', '--no-index', '--find-links', $projectWheelhouse,
        '-e', "$((Join-Path $ProjectRoot 'backend'))[dev,web,database]"
    )
}

if (-not $SkipFrontend) {
    $npmCache = Find-AgentEvalAsset -ReleaseRoot $ReleaseRoot -Directory -Patterns @(
        'frontend\npm-cache', 'npm-cache'
    )
    $projectNpmCache = Join-Path $cache 'npm-cache'
    New-Item -ItemType Directory -Force -Path $projectNpmCache | Out-Null
    Copy-AgentEvalDirectoryContents -Source $npmCache -Destination $projectNpmCache
    $npm = Join-Path $runtime 'node\npm.cmd'
    Invoke-AgentEvalCommand -FilePath $npm -ArgumentList @(
        'ci', '--offline', '--cache', $projectNpmCache
    ) -WorkingDirectory (Join-Path $ProjectRoot 'frontend')
}

foreach ($component in @('skill-up', 'multica')) {
    $releaseSource = Find-AgentEvalAsset -ReleaseRoot $ReleaseRoot -Directory -Patterns @(
        "sources\$component", "sources\$component-*"
    )
    $targetSource = Join-Path $sourceRoot $component
    if (-not (Test-Path -LiteralPath $targetSource)) {
        Copy-AgentEvalDirectoryContents -Source $releaseSource -Destination $targetSource
    } else {
        Write-Host "Preserving existing $component source: $targetSource"
    }
}

$skillUpBinary = Find-AgentEvalAsset -ReleaseRoot $ReleaseRoot -Patterns @(
    'prebuilt\skill-up.exe', 'bin\skill-up.exe'
)
$multicaBinary = Find-AgentEvalAsset -ReleaseRoot $ReleaseRoot -Patterns @(
    'prebuilt\multica-eval-runtime.exe', 'bin\multica-eval-runtime.exe'
)
Copy-Item -LiteralPath $skillUpBinary -Destination (Join-Path $tools 'skill-up.exe') -Force
Copy-Item -LiteralPath $multicaBinary -Destination (Join-Path $binRoot 'multica-eval-runtime.exe') -Force

$questionBank = Find-AgentEvalAsset -ReleaseRoot $ReleaseRoot -Optional -Patterns @(
    'question-bank\maeval-public.db', 'question-bank\maeval.db'
)
if ($questionBank) {
    $databaseDir = Join-Path $ProjectRoot 'backend\model_eval_data'
    $database = Join-Path $databaseDir 'maeval.db'
    New-Item -ItemType Directory -Force -Path $databaseDir | Out-Null
    if (-not (Test-Path -LiteralPath $database)) {
        Copy-Item -LiteralPath $questionBank -Destination $database
    } else {
        Write-Host "Preserving existing question bank: $database"
    }
}

if (-not $SkipJustDo) {
    $justDoInstaller = Find-AgentEvalAsset -ReleaseRoot $ReleaseRoot -Optional -Patterns @(
        'justdo\JustDo Setup *.exe', 'installers\JustDo Setup *.exe', 'JustDo Setup *.exe'
    )
    if ($justDoInstaller) {
        Write-Host 'Installing JustDo...' -ForegroundColor Cyan
        $justDoProcess = if ($InteractiveJustDo) {
            Start-Process -FilePath $justDoInstaller -Wait -PassThru
        } else {
            Start-Process -FilePath $justDoInstaller -ArgumentList @('/S') -Wait -PassThru
        }
        if ($justDoProcess.ExitCode -ne 0) { throw "JustDo installer exited with code $($justDoProcess.ExitCode)." }
    } else {
        Write-Warning 'JustDo installer was not present in the release input.'
    }
    $justDoCandidates = @(
        (Join-Path $env:APPDATA 'JustDo\multica\development\JustDo-agent.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\JustDo\resources\JustDo-agent.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\JustDo\JustDo-agent.exe')
    )
    $justDo = $justDoCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $justDo) {
        foreach ($searchRoot in @(
            (Join-Path $env:LOCALAPPDATA 'Programs\JustDo'),
            (Join-Path $env:LOCALAPPDATA 'JustDo'),
            (Join-Path $env:APPDATA 'JustDo')
        )) {
            if (-not (Test-Path -LiteralPath $searchRoot)) { continue }
            $justDo = Get-ChildItem -LiteralPath $searchRoot -Filter 'JustDo-agent.exe' -File -Recurse -ErrorAction SilentlyContinue |
                Select-Object -First 1 -ExpandProperty FullName
            if ($justDo) { break }
        }
    }
    if ($justDo) {
        $portableJustDo = $justDo.Replace('\', '/')
        Set-AgentEvalEnvValue -ProjectRoot $ProjectRoot -Name 'JUSTDO_AGENT_EXECUTABLE' -Value $portableJustDo
        $agentPaths = @{}
        $existingPaths = [Environment]::GetEnvironmentVariable('AGENT_PATHS_JSON')
        if (-not [string]::IsNullOrWhiteSpace($existingPaths)) {
            try {
                $parsedPaths = $existingPaths | ConvertFrom-Json
                foreach ($property in $parsedPaths.PSObject.Properties) {
                    $agentPaths[$property.Name] = [string]$property.Value
                }
            } catch {
                Write-Warning 'Existing AGENT_PATHS_JSON is invalid; only the detected JustDo path will be written.'
            }
        }
        $agentPaths['justdo'] = $portableJustDo
        Set-AgentEvalEnvValue -ProjectRoot $ProjectRoot -Name 'AGENT_PATHS_JSON' -Value ($agentPaths | ConvertTo-Json -Compress)
    } else {
        Write-Warning 'JustDo-agent.exe was not found automatically; set JUSTDO_AGENT_EXECUTABLE in .env manually.'
    }
}

Set-AgentEvalEnvValue -ProjectRoot $ProjectRoot -Name 'PYTHON_EXECUTABLE' -Value 'backend/.runtime/windows/python/Scripts/python.exe'
Set-AgentEvalEnvValue -ProjectRoot $ProjectRoot -Name 'NODE_EXECUTABLE' -Value 'backend/.runtime/windows/node/node.exe'
Set-AgentEvalEnvValue -ProjectRoot $ProjectRoot -Name 'NPM_EXECUTABLE' -Value 'backend/.runtime/windows/node/npm.cmd'
Set-AgentEvalEnvValue -ProjectRoot $ProjectRoot -Name 'GO_EXECUTABLE' -Value 'backend/.runtime/windows/go/bin/go.exe'
Set-AgentEvalEnvValue -ProjectRoot $ProjectRoot -Name 'SKILLUP_EXECUTABLE' -Value 'backend/.tools/windows/skill-up.exe'
Set-AgentEvalEnvValue -ProjectRoot $ProjectRoot -Name 'MULTICA_EXECUTABLE' -Value 'backend/.runtime/windows/bin/multica-eval-runtime.exe'

if (-not $SkipVerify) { & (Join-Path $ProjectRoot 'doctor.ps1') }
Write-Host 'WINDOWS_OFFLINE_INSTALL_OK' -ForegroundColor Green
Write-Host "Edit configuration: $envFile"
Write-Host 'Start services: .\start.ps1'
