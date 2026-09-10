[CmdletBinding()]
param([switch]$Test)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\windows\common.ps1')

$projectRoot = Get-AgentEvalProjectRoot -StartPath $PSScriptRoot
Import-AgentEvalEnv -ProjectRoot $projectRoot | Out-Null
$go = Get-AgentEvalConfiguredPath -ProjectRoot $projectRoot -Name 'GO_EXECUTABLE' -Default 'backend/.runtime/windows/go/bin/go.exe'
$source = Join-Path $projectRoot 'backend\.runtime\windows\src\multica'
$server = Join-Path $source 'server'
$binary = Get-AgentEvalConfiguredPath -ProjectRoot $projectRoot -Name 'MULTICA_EXECUTABLE' -Default 'backend/.runtime/windows/bin/multica-eval-runtime.exe'
$vendor = Join-Path $server 'vendor'
$runnerSource = Join-Path $projectRoot 'backend\runtime\multica-local-runner'
$runnerTarget = Join-Path $server 'cmd\multica-eval-runtime'

if (-not (Test-Path -LiteralPath $go)) { throw "Go was not found: $go" }
if (-not (Test-Path -LiteralPath (Join-Path $server 'go.mod'))) { throw "Multica server source was not found: $server" }
if (-not (Test-Path -LiteralPath $vendor)) {
    throw 'Multica server/vendor is missing. The offline release must include vendored Go dependencies.'
}
New-Item -ItemType Directory -Force -Path $runnerTarget | Out-Null
Copy-Item -LiteralPath (Join-Path $runnerSource 'main.go') -Destination $runnerTarget -Force
Copy-Item -LiteralPath (Join-Path $runnerSource 'main_test.go') -Destination $runnerTarget -Force
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $binary) | Out-Null

$temporaryBinary = "$binary.new"
Invoke-AgentEvalCommand -FilePath $go -WorkingDirectory $server -ArgumentList @(
    'build', '-mod=vendor', '-trimpath', '-o', $temporaryBinary, '.\cmd\multica-eval-runtime'
)
if ($Test) {
    Invoke-AgentEvalCommand -FilePath $go -WorkingDirectory $server -ArgumentList @(
        'test', '-mod=vendor', '.\cmd\multica-eval-runtime'
    )
}
Move-Item -LiteralPath $temporaryBinary -Destination $binary -Force
Write-Host "MULTICA_BUILD_OK: $binary" -ForegroundColor Green
