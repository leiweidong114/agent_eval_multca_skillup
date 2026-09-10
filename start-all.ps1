[CmdletBinding()]
param(
    [int]$BackendStartPort = 8000,
    [int]$FrontendStartPort = 5173,
    [int]$MaxPortAttempts = 200,
    [switch]$OpenBrowser
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = $PSScriptRoot
. (Join-Path $ProjectRoot 'scripts\windows\common.ps1')
$EnvValues = Import-AgentEvalEnv -ProjectRoot $ProjectRoot
if (-not $PSBoundParameters.ContainsKey('BackendStartPort') -and $EnvValues.ContainsKey('BACKEND_PORT')) {
    $BackendStartPort = [int]$EnvValues['BACKEND_PORT']
}
if (-not $PSBoundParameters.ContainsKey('FrontendStartPort') -and $EnvValues.ContainsKey('FRONTEND_PORT')) {
    $FrontendStartPort = [int]$EnvValues['FRONTEND_PORT']
}
$BackendHost = if ($EnvValues.ContainsKey('BACKEND_HOST')) { $EnvValues['BACKEND_HOST'] } else { '127.0.0.1' }
$FrontendHost = if ($EnvValues.ContainsKey('FRONTEND_HOST')) { $EnvValues['FRONTEND_HOST'] } else { '127.0.0.1' }
$StartTimeout = if ($EnvValues.ContainsKey('SERVICE_START_TIMEOUT_SECONDS')) { [int]$EnvValues['SERVICE_START_TIMEOUT_SECONDS'] } else { 45 }
$RuntimeDirectory = Join-Path $ProjectRoot 'backend\.runtime\service-manager'
$StateFile = Join-Path $RuntimeDirectory 'services.json'
$StopScript = Join-Path $ProjectRoot 'stop-all.ps1'

function Test-ProcessAlive {
    param([object]$Service)

    if ($null -eq $Service -or $null -eq $Service.pid) {
        return $false
    }
    return $null -ne (Get-Process -Id ([int]$Service.pid) -ErrorAction SilentlyContinue)
}

function Test-PortAvailable {
    param([int]$Port, [string]$HostAddress)

    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new(
            [System.Net.IPAddress]::Parse($HostAddress),
            $Port
        )
        $listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        if ($null -ne $listener) {
            $listener.Stop()
        }
    }
}

function Find-FreePort {
    param(
        [int]$StartPort,
        [string]$HostAddress,
        [int[]]$ExcludedPorts = @()
    )

    for ($offset = 0; $offset -lt $MaxPortAttempts; $offset++) {
        $candidate = $StartPort + $offset
        if ($candidate -gt 65535) {
            break
        }
        if ($ExcludedPorts -contains $candidate) {
            continue
        }
        if (Test-PortAvailable -Port $candidate -HostAddress $HostAddress) {
            return $candidate
        }
    }
    throw "No free TCP port was found from $StartPort within $MaxPortAttempts attempts."
}

function Wait-HttpReady {
    param(
        [string]$Uri,
        [System.Diagnostics.Process]$Process,
        [int]$TimeoutSeconds = 45
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if ($Process.HasExited) {
            throw "Process $($Process.Id) exited before $Uri became ready (exit code $($Process.ExitCode))."
        }
        try {
            $response = Invoke-WebRequest -Uri $Uri -TimeoutSec 2 -UseBasicParsing
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return
            }
        }
        catch {
            Start-Sleep -Milliseconds 300
        }
    } while ([DateTime]::UtcNow -lt $deadline)

    throw "Timed out waiting for $Uri."
}

function New-ServiceRecord {
    param(
        [string]$Name,
        [System.Diagnostics.Process]$Process,
        [int]$Port,
        [string]$Url,
        [string]$CommandMarker,
        [string]$StdoutLog,
        [string]$StderrLog
    )

    $createdAt = $Process.StartTime.ToUniversalTime().ToString('o')
    return [ordered]@{
        name = $Name
        pid = $Process.Id
        port = $Port
        url = $Url
        created_at_utc = $createdAt
        command_marker = $CommandMarker
        stdout_log = $StdoutLog
        stderr_log = $StderrLog
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'backend\run_server.py'))) {
    throw "Backend entry point was not found under $ProjectRoot."
}
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'frontend\package.json'))) {
    throw "Frontend package.json was not found under $ProjectRoot."
}

New-Item -ItemType Directory -Path $RuntimeDirectory -Force | Out-Null

if (Test-Path -LiteralPath $StateFile) {
    try {
        $previous = Get-Content -Raw -LiteralPath $StateFile | ConvertFrom-Json
        $running = @($previous.services | Where-Object { Test-ProcessAlive -Service $_ })
        if ($running.Count -eq @($previous.services).Count -and $running.Count -gt 0) {
            Write-Host 'Agent Eval services are already running:' -ForegroundColor Yellow
            foreach ($service in $running) {
                Write-Host ("  {0}: {1} (PID {2})" -f $service.name, $service.url, $service.pid)
            }
            exit 0
        }
        & $StopScript -Quiet
    }
    catch {
        Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
    }
}

$ConfiguredPython = Get-AgentEvalConfiguredPath -ProjectRoot $ProjectRoot -Name 'PYTHON_EXECUTABLE' -Default 'backend/.runtime/windows/python/Scripts/python.exe'
$PythonCandidates = @(
    $ConfiguredPython,
    (Join-Path $ProjectRoot 'backend\.runtime\windows\python\Scripts\python.exe'),
    (Join-Path $ProjectRoot 'backend\.runtime\windows\python\python.exe')
)
$PythonExecutable = $PythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $PythonExecutable) {
    $PythonExecutable = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $PythonExecutable) {
    throw 'Python was not found. Run backend\scripts\setup_windows.ps1 first.'
}

$NodeExecutable = Get-AgentEvalConfiguredPath -ProjectRoot $ProjectRoot -Name 'NODE_EXECUTABLE' -Default 'backend/.runtime/windows/node/node.exe'
if (-not (Test-Path -LiteralPath $NodeExecutable)) {
    $NodeExecutable = (Get-Command node -ErrorAction SilentlyContinue).Source
}
$ViteEntry = Join-Path $ProjectRoot 'frontend\node_modules\vite\bin\vite.js'
if (-not $NodeExecutable) {
    throw 'Node.js was not found in PATH.'
}
if (-not (Test-Path -LiteralPath $ViteEntry)) {
    throw 'Frontend dependencies are missing. Run npm install under frontend first.'
}

$BackendPort = Find-FreePort -StartPort $BackendStartPort -HostAddress $BackendHost
$FrontendPort = Find-FreePort -StartPort $FrontendStartPort -HostAddress $FrontendHost -ExcludedPorts @($BackendPort)
$BackendProbeHost = if ($BackendHost -in @('0.0.0.0', '::')) { '127.0.0.1' } else { $BackendHost }
$FrontendProbeHost = if ($FrontendHost -in @('0.0.0.0', '::')) { '127.0.0.1' } else { $FrontendHost }
$BackendUrl = "http://$BackendProbeHost`:$BackendPort"
$FrontendUrl = "http://$FrontendProbeHost`:$FrontendPort"
$BackendStdout = Join-Path $RuntimeDirectory 'backend.stdout.log'
$BackendStderr = Join-Path $RuntimeDirectory 'backend.stderr.log'
$FrontendStdout = Join-Path $RuntimeDirectory 'frontend.stdout.log'
$FrontendStderr = Join-Path $RuntimeDirectory 'frontend.stderr.log'
$StartedProcesses = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()

try {
    $BackendProcess = Start-Process `
        -FilePath $PythonExecutable `
        -ArgumentList @(
            (Join-Path $ProjectRoot 'backend\run_server.py'),
            '--host', $BackendHost,
            '--port', $BackendPort
        ) `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput $BackendStdout `
        -RedirectStandardError $BackendStderr `
        -WindowStyle Hidden `
        -PassThru
    $StartedProcesses.Add($BackendProcess)
    Wait-HttpReady -Uri "$BackendUrl/api/health" -Process $BackendProcess -TimeoutSeconds $StartTimeout

    $PreviousApiTarget = $env:VITE_API_TARGET
    $env:VITE_API_TARGET = $BackendUrl
    try {
        $FrontendProcess = Start-Process `
            -FilePath $NodeExecutable `
            -ArgumentList @(
                $ViteEntry,
                '--host', $FrontendHost,
                '--port', $FrontendPort,
                '--strictPort'
            ) `
            -WorkingDirectory (Join-Path $ProjectRoot 'frontend') `
            -RedirectStandardOutput $FrontendStdout `
            -RedirectStandardError $FrontendStderr `
            -WindowStyle Hidden `
            -PassThru
    }
    finally {
        $env:VITE_API_TARGET = $PreviousApiTarget
    }
    $StartedProcesses.Add($FrontendProcess)
    Wait-HttpReady -Uri $FrontendUrl -Process $FrontendProcess -TimeoutSeconds $StartTimeout

    $State = [ordered]@{
        schema_version = 1
        project_root = $ProjectRoot
        started_at_utc = [DateTime]::UtcNow.ToString('o')
        services = @(
            (New-ServiceRecord -Name 'backend' -Process $BackendProcess -Port $BackendPort `
                -Url $BackendUrl -CommandMarker 'backend\run_server.py' `
                -StdoutLog $BackendStdout -StderrLog $BackendStderr),
            (New-ServiceRecord -Name 'frontend' -Process $FrontendProcess -Port $FrontendPort `
                -Url $FrontendUrl -CommandMarker 'vite\bin\vite.js' `
                -StdoutLog $FrontendStdout -StderrLog $FrontendStderr)
        )
    }
    $State | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $StateFile -Encoding UTF8

    Write-Host 'Agent Eval services started successfully.' -ForegroundColor Green
    Write-Host "  Frontend: $FrontendUrl (PID $($FrontendProcess.Id))"
    Write-Host "  Backend:  $BackendUrl (PID $($BackendProcess.Id))"
    Write-Host "  API docs: $BackendUrl/docs"
    Write-Host "  State:    $StateFile"
    Write-Host "  Stop:     .\stop-all.ps1"

    if ($OpenBrowser) {
        Start-Process $FrontendUrl | Out-Null
    }
}
catch {
    foreach ($process in $StartedProcesses) {
        if (-not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Error $_
    Write-Host "Backend log:  $BackendStderr"
    Write-Host "Frontend log: $FrontendStderr"
    exit 1
}
