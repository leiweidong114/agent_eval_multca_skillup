[CmdletBinding()]
param([switch]$Quiet)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Key = Join-Path $ProjectRoot '.secrets\agent-eval-infra-ed25519-v3'
$RuntimeDirectory = Join-Path $ProjectRoot 'backend\.runtime\service-manager'
$StateFile = Join-Path $RuntimeDirectory 'infrastructure-tunnel.json'
if (-not (Test-Path -LiteralPath $Key)) {
    if (-not $Quiet) { Write-Host 'Infrastructure SSH key is not configured; tunnel was skipped.' -ForegroundColor Yellow }
    exit 0
}
New-Item -ItemType Directory -Force -Path $RuntimeDirectory | Out-Null

if (Test-Path -LiteralPath $StateFile) {
    try {
        $state = Get-Content -Raw -LiteralPath $StateFile | ConvertFrom-Json
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$state.pid)" -ErrorAction SilentlyContinue
        if ($process -and ([string]$process.CommandLine).Contains('agent-eval-infra-ed25519-v3')) {
            if (-not $Quiet) { Write-Host "Infrastructure tunnel is already running (PID $($state.pid))." }
            exit 0
        }
    } catch {}
    Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
}

$arguments = @(
    '-N', '-T', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes',
    '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3',
    '-i', $Key,
    '-L', '18848:127.0.0.1:8848',
    '-L', '27018:127.0.0.1:27017',
    '-L', '16379:127.0.0.1:6379',
    'agent_eval_tunnel@8.137.196.46'
)
$process = Start-Process -FilePath 'ssh.exe' -ArgumentList $arguments -WindowStyle Hidden -PassThru
try {
    $deadline = [DateTime]::UtcNow.AddSeconds(12)
    do {
        if ($process.HasExited) { throw "SSH tunnel exited with code $($process.ExitCode)." }
        $ready = $true
        foreach ($port in 18848, 27018, 16379) {
            $client = [Net.Sockets.TcpClient]::new()
            try { $client.Connect('127.0.0.1', $port) } catch { $ready = $false } finally { $client.Dispose() }
        }
        if ($ready) { break }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    if (-not $ready) { throw 'Infrastructure SSH tunnel did not become ready.' }
    [ordered]@{ pid = $process.Id; created_at_utc = $process.StartTime.ToUniversalTime().ToString('o') } |
        ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding UTF8
    if (-not $Quiet) { Write-Host "Infrastructure tunnel started (PID $($process.Id))." -ForegroundColor Green }
} catch {
    if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    throw
}
