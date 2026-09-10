[CmdletBinding()]
param([switch]$RequireConfiguration)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\windows\common.ps1')
$projectRoot = Get-AgentEvalProjectRoot -StartPath $PSScriptRoot
$values = Import-AgentEvalEnv -ProjectRoot $projectRoot

$checks = @(
    @{ Name='Python'; Path=(Get-AgentEvalConfiguredPath -ProjectRoot $projectRoot -Name 'PYTHON_EXECUTABLE' -Default 'backend/.runtime/windows/python/Scripts/python.exe') },
    @{ Name='Node'; Path=(Get-AgentEvalConfiguredPath -ProjectRoot $projectRoot -Name 'NODE_EXECUTABLE' -Default 'backend/.runtime/windows/node/node.exe') },
    @{ Name='npm'; Path=(Get-AgentEvalConfiguredPath -ProjectRoot $projectRoot -Name 'NPM_EXECUTABLE' -Default 'backend/.runtime/windows/node/npm.cmd') },
    @{ Name='Go'; Path=(Get-AgentEvalConfiguredPath -ProjectRoot $projectRoot -Name 'GO_EXECUTABLE' -Default 'backend/.runtime/windows/go/bin/go.exe') },
    @{ Name='Skill-Up'; Path=(Get-AgentEvalConfiguredPath -ProjectRoot $projectRoot -Name 'SKILLUP_EXECUTABLE' -Default 'backend/.tools/windows/skill-up.exe') },
    @{ Name='Multica runtime'; Path=(Get-AgentEvalConfiguredPath -ProjectRoot $projectRoot -Name 'MULTICA_EXECUTABLE' -Default 'backend/.runtime/windows/bin/multica-eval-runtime.exe') },
    @{ Name='Frontend modules'; Path=(Join-Path $projectRoot 'frontend\node_modules\vite\bin\vite.js') }
)

$failed = $false
foreach ($check in $checks) {
    $ok = Test-Path -LiteralPath $check.Path
    if (-not $ok) { $failed = $true }
    $color = if ($ok) { 'Green' } else { 'Red' }
    Write-Host ("[{0}] {1}: {2}" -f $(if ($ok) {'OK'} else {'MISSING'}), $check.Name, $check.Path) -ForegroundColor $color
}

if ($RequireConfiguration) {
    foreach ($name in @('LITELLM_API_BASE', 'LITELLM_API_KEY', 'LITELLM_MASTER_KEY')) {
        if (-not $values.ContainsKey($name) -or [string]::IsNullOrWhiteSpace([string]$values[$name])) {
            Write-Host "[MISSING] .env: $name" -ForegroundColor Red
            $failed = $true
        }
    }
    $hasDatabaseUrl = $values.ContainsKey('DATABASE_URL') -and -not [string]::IsNullOrWhiteSpace([string]$values['DATABASE_URL'])
    $hasDatabaseFields = $values.ContainsKey('DATABASE_HOST') -and $values.ContainsKey('DATABASE_USER') -and $values.ContainsKey('DATABASE_PASSWORD')
    if (-not $hasDatabaseUrl -and -not $hasDatabaseFields) {
        Write-Host '[MISSING] .env: DATABASE_URL or DATABASE_HOST/USER/PASSWORD' -ForegroundColor Red
        $failed = $true
    }
}

if ($failed) { throw 'Environment check failed.' }
$python = Get-AgentEvalConfiguredPath -ProjectRoot $projectRoot -Name 'PYTHON_EXECUTABLE' -Default 'backend/.runtime/windows/python/Scripts/python.exe'
Invoke-WithAgentEvalPythonIsolation {
    Invoke-AgentEvalCommand -FilePath $python -WorkingDirectory (Join-Path $projectRoot 'backend') -ArgumentList @('-m', 'agent_eval.cli', 'doctor')
}
Write-Host 'ENVIRONMENT_DOCTOR_OK' -ForegroundColor Green
