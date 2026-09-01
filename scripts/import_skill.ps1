param([Parameter(Mandatory=$true)][string]$Path)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$source = (Resolve-Path $Path).Path
if (-not (Test-Path -LiteralPath (Join-Path $source "SKILL.md"))) {
    throw "The selected directory does not contain SKILL.md: $source"
}
$name = Split-Path -Leaf $source
if ($name -notmatch '^[a-z0-9][a-z0-9-]{0,62}$') {
    throw "Skill directory name must contain lowercase letters, digits and hyphens: $name"
}
$target = Join-Path $projectRoot "backend\skills\$name"
if (Test-Path -LiteralPath $target) { throw "Skill already exists: $target" }
Copy-Item -LiteralPath $source -Destination $target -Recurse
Write-Host "Imported Skill: $target"
