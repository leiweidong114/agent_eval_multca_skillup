[CmdletBinding()]
param(
    [switch]$Quiet
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = $PSScriptRoot
$RuntimeDirectory = Join-Path $ProjectRoot 'backend\.runtime\service-manager'
$StateFile = Join-Path $RuntimeDirectory 'services.json'

function Get-ManagedProcess {
    param([object]$Service)

    $processId = [int]$Service.pid
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $null
    }

    $marker = [string]$Service.command_marker
    if (
        $marker -and
        ([string]$process.CommandLine).IndexOf(
            $marker,
            [StringComparison]::OrdinalIgnoreCase
        ) -lt 0
    ) {
        if (-not $Quiet) {
            Write-Warning "PID $processId no longer matches $($Service.name); it will not be stopped."
        }
        return $null
    }

    if ($Service.created_at_utc) {
        $expected = if ($Service.created_at_utc -is [DateTime]) {
            $Service.created_at_utc.ToUniversalTime()
        }
        else {
            [DateTime]::Parse(
                [string]$Service.created_at_utc,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::RoundtripKind
            ).ToUniversalTime()
        }
        $actualProcess = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($null -eq $actualProcess) {
            return $null
        }
        $actual = $actualProcess.StartTime.ToUniversalTime()
        if ([Math]::Abs(($actual - $expected).TotalSeconds) -gt 3) {
            if (-not $Quiet) {
                Write-Warning "PID $processId was reused by another process; it will not be stopped."
            }
            return $null
        }
    }
    return $process
}

function Stop-ManagedProcessTree {
    param([object]$Service)

    $root = Get-ManagedProcess -Service $Service
    if ($null -eq $root) {
        return $false
    }

    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $pending = [System.Collections.Generic.Queue[int]]::new()
    $pending.Enqueue([int]$root.ProcessId)
    $descendants = [System.Collections.Generic.List[int]]::new()
    while ($pending.Count -gt 0) {
        $parent = $pending.Dequeue()
        foreach ($child in $all | Where-Object { [int]$_.ParentProcessId -eq $parent }) {
            $childId = [int]$child.ProcessId
            $descendants.Add($childId)
            $pending.Enqueue($childId)
        }
    }

    for ($index = $descendants.Count - 1; $index -ge 0; $index--) {
        Stop-Process -Id $descendants[$index] -Force -ErrorAction SilentlyContinue
    }
    Stop-Process -Id ([int]$root.ProcessId) -Force -ErrorAction SilentlyContinue
    return $true
}

if (-not (Test-Path -LiteralPath $StateFile)) {
    if (-not $Quiet) {
        Write-Host 'No services started by start-all.ps1 were found.' -ForegroundColor Yellow
    }
    exit 0
}

try {
    $State = Get-Content -Raw -LiteralPath $StateFile | ConvertFrom-Json
}
catch {
    Write-Error "The service state file is invalid: $StateFile"
    exit 1
}

$Stopped = 0
foreach ($service in @($State.services)) {
    if (Stop-ManagedProcessTree -Service $service) {
        $Stopped++
        if (-not $Quiet) {
            Write-Host ("Stopped {0} (PID {1}, port {2})." -f $service.name, $service.pid, $service.port)
        }
    }
}

Remove-Item -LiteralPath $StateFile -Force
if (-not $Quiet) {
    if ($Stopped -gt 0) {
        Write-Host 'Agent Eval services stopped.' -ForegroundColor Green
    }
    else {
        Write-Host 'Managed services were already stopped; stale state was removed.' -ForegroundColor Yellow
    }
}
