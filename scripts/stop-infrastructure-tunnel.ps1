[CmdletBinding()]
param([switch]$Quiet)

Set-StrictMode -Version Latest
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$StateFile = Join-Path $ProjectRoot 'backend\.runtime\service-manager\infrastructure-tunnel.json'
if (-not (Test-Path -LiteralPath $StateFile)) { exit 0 }
try {
    $state = Get-Content -Raw -LiteralPath $StateFile | ConvertFrom-Json
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$state.pid)" -ErrorAction SilentlyContinue
    if ($process -and ([string]$process.CommandLine).Contains('agent-eval-infra-ed25519-v3')) {
        Stop-Process -Id ([int]$state.pid) -Force -ErrorAction SilentlyContinue
        if (-not $Quiet) { Write-Host "Infrastructure tunnel stopped (PID $($state.pid))." }
    }
} finally {
    Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
}
