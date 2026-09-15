[CmdletBinding()]
param(
    [string]$ClientEnvironmentFile = (Join-Path $PSScriptRoot '..\.secrets\nacos-client.env')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Target = Join-Path $ProjectRoot '.env'
if (-not (Test-Path -LiteralPath $ClientEnvironmentFile)) {
    throw "Nacos client configuration was not found: $ClientEnvironmentFile"
}

$updates = [ordered]@{}
foreach ($line in Get-Content -LiteralPath $ClientEnvironmentFile) {
    if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
    $name, $value = $line -split '=', 2
    if ($name -notmatch '^NACOS_[A-Z0-9_]+$') { continue }
    $updates[$name] = $value
}
if (-not $updates.Contains('NACOS_SERVER_ADDR') -or -not $updates.Contains('NACOS_USERNAME')) {
    throw 'The Nacos client configuration is incomplete.'
}

$lines = [System.Collections.Generic.List[string]]::new()
if (Test-Path -LiteralPath $Target) {
    foreach ($line in Get-Content -LiteralPath $Target) { $lines.Add($line) }
}
foreach ($entry in $updates.GetEnumerator()) {
    $prefix = "$($entry.Key)="
    $found = $false
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index].StartsWith($prefix, [StringComparison]::Ordinal)) {
            $lines[$index] = "$prefix$($entry.Value)"
            $found = $true
            break
        }
    }
    if (-not $found) { $lines.Add("$prefix$($entry.Value)") }
}
$lines | Set-Content -LiteralPath $Target -Encoding UTF8
Write-Host 'Nacos bootstrap settings were written to the ignored .env file.' -ForegroundColor Green
