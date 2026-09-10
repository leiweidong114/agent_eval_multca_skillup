Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-AgentEvalProjectRoot {
    param([string]$StartPath = $PSScriptRoot)

    $current = [System.IO.Path]::GetFullPath($StartPath)
    while ($true) {
        if (
            (Test-Path -LiteralPath (Join-Path $current 'backend\pyproject.toml')) -and
            (Test-Path -LiteralPath (Join-Path $current 'frontend\package.json'))
        ) {
            return $current
        }
        $parent = Split-Path -Parent $current
        if (-not $parent -or $parent -eq $current) {
            throw "Agent Eval project root was not found from: $StartPath"
        }
        $current = $parent
    }
}

function Import-AgentEvalEnv {
    param([Parameter(Mandatory)][string]$ProjectRoot)

    $envFile = Join-Path $ProjectRoot '.env'
    if (-not (Test-Path -LiteralPath $envFile)) {
        return @{}
    }

    $values = @{}
    foreach ($rawLine in Get-Content -LiteralPath $envFile -Encoding UTF8) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith('#')) { continue }
        if ($line.StartsWith('export ')) { $line = $line.Substring(7).TrimStart() }
        $separator = $line.IndexOf('=')
        if ($separator -lt 1) { throw "Invalid .env entry: $rawLine" }
        $name = $line.Substring(0, $separator).Trim()
        if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
            throw "Invalid .env variable name: $name"
        }
        $value = $line.Substring($separator + 1).Trim()
        if ($value.Length -ge 2 -and (
            ($value[0] -eq '"' -and $value[$value.Length - 1] -eq '"') -or
            ($value[0] -eq "'" -and $value[$value.Length - 1] -eq "'")
        )) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $values[$name] = $value
        Set-Item -LiteralPath "Env:$name" -Value $value
    }
    return $values
}

function Resolve-AgentEvalPath {
    param(
        [Parameter(Mandatory)][string]$ProjectRoot,
        [Parameter(Mandatory)][string]$Value
    )

    $expanded = [Environment]::ExpandEnvironmentVariables($Value.Trim().Trim('"').Trim("'"))
    if ([System.IO.Path]::IsPathRooted($expanded)) {
        return [System.IO.Path]::GetFullPath($expanded)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $expanded))
}

function Assert-AgentEvalChildPath {
    param(
        [Parameter(Mandatory)][string]$Parent,
        [Parameter(Mandatory)][string]$Child
    )

    $parentPath = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    $childPath = [System.IO.Path]::GetFullPath($Child)
    if (-not $childPath.StartsWith($parentPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside $Parent`: $Child"
    }
    return $childPath
}

function Copy-AgentEvalDirectoryContents {
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Destination
    )

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    & robocopy.exe $Source $Destination /E /COPY:DAT /DCOPY:DAT /R:2 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    $robocopyExitCode = $LASTEXITCODE
    if ($robocopyExitCode -ge 8) {
        throw "robocopy failed with exit code $robocopyExitCode`: $Source -> $Destination"
    }
}

function Get-AgentEvalConfiguredPath {
    param(
        [Parameter(Mandatory)][string]$ProjectRoot,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Default
    )

    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) { $value = $Default }
    return Resolve-AgentEvalPath -ProjectRoot $ProjectRoot -Value $value
}

function Set-AgentEvalEnvValue {
    param(
        [Parameter(Mandatory)][string]$ProjectRoot,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Value
    )

    $envFile = Join-Path $ProjectRoot '.env'
    $lines = if (Test-Path -LiteralPath $envFile) {
        @(Get-Content -LiteralPath $envFile -Encoding UTF8)
    } else { @() }
    $replacement = "$Name=$Value"
    $updated = $false
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index] -match "^\s*$([regex]::Escape($Name))\s*=") {
            $lines[$index] = $replacement
            $updated = $true
            break
        }
    }
    if (-not $updated) { $lines += $replacement }
    Set-Content -LiteralPath $envFile -Value $lines -Encoding UTF8
    Set-Item -LiteralPath "Env:$Name" -Value $Value
}

function Find-AgentEvalAsset {
    param(
        [Parameter(Mandatory)][string]$ReleaseRoot,
        [Parameter(Mandatory)][string[]]$Patterns,
        [switch]$Directory,
        [switch]$Optional
    )

    foreach ($pattern in $Patterns) {
        $candidate = Join-Path $ReleaseRoot $pattern
        if (
            -not [System.Management.Automation.WildcardPattern]::ContainsWildcardCharacters($candidate) -and
            (Test-Path -LiteralPath $candidate)
        ) {
            $matches = @(Get-Item -LiteralPath $candidate)
        } else {
            $matches = @(Get-ChildItem -Path $candidate -ErrorAction SilentlyContinue)
        }
        if ($Directory) { $matches = @($matches | Where-Object { $_.PSIsContainer }) }
        else { $matches = @($matches | Where-Object { -not $_.PSIsContainer }) }
        if ($matches.Count -gt 0) { return $matches[0].FullName }
    }
    if ($Optional) { return $null }
    throw "Required release asset was not found. Tried: $($Patterns -join ', ')"
}

function Invoke-AgentEvalCommand {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [string]$WorkingDirectory
    )

    if ($WorkingDirectory) { Push-Location $WorkingDirectory }
    try {
        & $FilePath @ArgumentList
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed with exit code $LASTEXITCODE`: $FilePath $($ArgumentList -join ' ')"
        }
    }
    finally {
        if ($WorkingDirectory) { Pop-Location }
    }
}
