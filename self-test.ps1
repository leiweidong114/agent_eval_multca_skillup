[CmdletBinding()]
param(
    [string[]]$Agent = @(),
    [string[]]$Model = @(),
    [int]$Timeout = 180,
    [int]$ModelWorkers = 2,
    [switch]$ProbeAllModels,
    [switch]$Strict,
    [switch]$NoFailExit,
    [string]$ReportRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = $PSScriptRoot
. (Join-Path $ProjectRoot 'scripts\windows\common.ps1')
$ProjectRoot = Get-AgentEvalProjectRoot -StartPath $ProjectRoot

if ($Timeout -lt 10) { throw '-Timeout must be at least 10 seconds.' }
if ($ModelWorkers -lt 1 -or $ModelWorkers -gt 32) {
    throw '-ModelWorkers must be between 1 and 32.'
}

if ([string]::IsNullOrWhiteSpace($ReportRoot)) {
    $ReportRoot = Join-Path $ProjectRoot 'backend\.runtime\self-test'
} else {
    $ReportRoot = Resolve-AgentEvalPath -ProjectRoot $ProjectRoot -Value $ReportRoot
}
$Timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$ReportDirectory = Join-Path $ReportRoot $Timestamp
New-Item -ItemType Directory -Path $ReportDirectory -Force | Out-Null

$Issues = [System.Collections.ArrayList]::new()
$Steps = [ordered]@{}
$EnvironmentValues = @{}
$EnvironmentError = $null
try {
    $EnvironmentValues = Import-AgentEvalEnv -ProjectRoot $ProjectRoot
} catch {
    $EnvironmentError = $_.Exception.Message
}

$SensitiveNames = @(
    'LITELLM_API_KEY', 'LITELLM_MASTER_KEY', 'LITELLM_PASSWORD',
    'DATABASE_PASSWORD', 'LITELLM_DATABASE_PASSWORD', 'DATABASE_URL',
    'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN'
)
$SensitiveValues = @(
    foreach ($Name in $SensitiveNames) {
        if ($EnvironmentValues.ContainsKey($Name)) {
            $Value = [string]$EnvironmentValues[$Name]
            if ($Value.Length -ge 4) { $Value }
        }
    }
)

function Protect-DiagnosticText {
    param([AllowEmptyString()][string]$Text)
    if ($null -eq $Text) { return '' }
    $Protected = $Text
    foreach ($Value in $SensitiveValues) {
        $Protected = [regex]::Replace(
            $Protected,
            [regex]::Escape([string]$Value),
            '[REDACTED]',
            [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
        )
    }
    $Protected = [regex]::Replace(
        $Protected,
        '(?i)(Bearer\s+)[A-Za-z0-9._~+\-/=]+',
        '$1[REDACTED]'
    )
    $Protected = [regex]::Replace(
        $Protected,
        '(?i)\bsk-[A-Za-z0-9._-]{8,}\b',
        '[REDACTED]'
    )
    return $Protected
}

function Add-DiagnosticIssue {
    param(
        [Parameter(Mandatory)][ValidateSet('error', 'warning', 'info')][string]$Severity,
        [Parameter(Mandatory)][string]$Component,
        [Parameter(Mandatory)][string]$Summary,
        [string]$Category = 'diagnostic',
        [string]$Detail = '',
        [string]$SuggestedAction = '',
        [string[]]$ConfigurationChanges = @(),
        $Evidence = $null
    )
    $Issues.Add([ordered]@{
        severity = $Severity
        component = $Component
        category = $Category
        summary = $Summary
        detail = Protect-DiagnosticText $Detail
        suggested_action = $SuggestedAction
        configuration_changes = @($ConfigurationChanges)
        evidence = $Evidence
    }) | Out-Null
}

function Get-DiagnosticProperty {
    param(
        $InputObject,
        [Parameter(Mandatory)][string]$Name
    )
    if ($null -eq $InputObject) { return $null }
    if ($InputObject -is [System.Collections.IDictionary]) {
        if ($InputObject.Contains($Name)) { return $InputObject[$Name] }
        return $null
    }
    $Property = $InputObject.PSObject.Properties[$Name]
    return $(if ($null -ne $Property) { $Property.Value } else { $null })
}

function ConvertTo-NativeArgument {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Value)
    if ($Value.Length -eq 0) { return '""' }
    if ($Value -notmatch '[\s"]') { return $Value }
    return '"' + $Value.Replace('"', '\"') + '"'
}

function Get-FailureConfigurationChanges {
    param(
        [string]$Category,
        [string]$Component
    )
    switch -Regex ($Category) {
        'authentication' {
            return @('Set a valid intranet Virtual Key in root .env: LITELLM_API_KEY=<key>.')
        }
        'authorization' {
            return @('Grant LITELLM_API_KEY access to the selected model; for strict checks, grant LITELLM_MASTER_KEY access to /key/generate and /key/delete.')
        }
        'model_(incompatible|protocol_incompatible)|unrecognized_model' {
            return @('Set LITELLM_MODEL/AGENT_TEST_MODEL to the exact LiteLLM deployment id and keep LITELLM_PROTOCOL=openai_compatible.')
        }
        'gateway_(unavailable|server_error)' {
            return @('Set LITELLM_API_BASE to a directly reachable intranet URL ending in /v1; verify firewall, DNS, proxy and TLS trust.')
        }
        'gateway_(quota_exhausted|rate_limited)' {
            return @('No local code change is required; replenish upstream quota or lower Agent/model concurrency.')
        }
        'postgresql|database_' {
            return @('Correct DATABASE_URL or DATABASE_HOST/PORT/NAME/USER/PASSWORD/SSLMODE in root .env and grant SELECT on public."LiteLLM_SpendLogs".')
        }
        'trace_key' {
            return @('Set LITELLM_MASTER_KEY to a key allowed to create and delete temporary LiteLLM keys.')
        }
        'agent_bridge' {
            return @('Set JUSTDO_AGENT_EXECUTABLE to the matching JustDo-agent.exe, then start the same-version JustDo desktop application and keep it running.')
        }
        'agent_timeout' {
            return @('Increase -Timeout only after confirming the gateway is progressing; otherwise inspect Agent stderr and LiteLLM logs.')
        }
        'command_failed' {
            if ($Component -like 'agent:*') {
                return @('Set AGENT_PATHS_JSON in root .env to the Agent executable absolute path, using forward slashes in Windows JSON.')
            }
        }
    }
    return @()
}

function Invoke-AgentEvalJson {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string[]]$Arguments
    )
    $Started = Get-Date
    if (-not $PythonExecutable -or -not (Test-Path -LiteralPath $PythonExecutable)) {
        return [ordered]@{
            command = $Name
            exit_code = $null
            duration_ms = 0
            success = $false
            data = $null
            error = 'Project Python runtime is missing.'
        }
    }

    $CommandArguments = @('-m', 'agent_eval.cli') + $Arguments
    $ArgumentText = ($CommandArguments | ForEach-Object {
        ConvertTo-NativeArgument -Value ([string]$_)
    }) -join ' '
    $ProcessInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $ProcessInfo.FileName = $PythonExecutable
    $ProcessInfo.Arguments = $ArgumentText
    $ProcessInfo.WorkingDirectory = Join-Path $ProjectRoot 'backend'
    $ProcessInfo.UseShellExecute = $false
    $ProcessInfo.CreateNoWindow = $true
    $ProcessInfo.RedirectStandardOutput = $true
    $ProcessInfo.RedirectStandardError = $true
    $ProcessInfo.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $ProcessInfo.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    foreach ($VariableName in @('PYTHONPATH', 'PYTHONHOME', 'PYTHONUSERBASE')) {
        $ProcessInfo.Environment.Remove($VariableName)
    }
    $ProcessInfo.Environment['PYTHONNOUSERSITE'] = '1'
    $ProcessInfo.Environment['PYTHONPATH'] = @(
        (Join-Path $ProjectRoot 'backend\src'),
        (Join-Path $ProjectRoot 'backend')
    ) -join [IO.Path]::PathSeparator

    $Process = [System.Diagnostics.Process]::new()
    $Process.StartInfo = $ProcessInfo
    try {
        $StartedOk = Invoke-WithAgentEvalPythonIsolation { $Process.Start() }
        if (-not $StartedOk) { throw "Failed to start $PythonExecutable" }
        $StdoutTask = $Process.StandardOutput.ReadToEndAsync()
        $StderrTask = $Process.StandardError.ReadToEndAsync()
        $Process.WaitForExit()
        $Stdout = Protect-DiagnosticText $StdoutTask.Result
        $Stderr = Protect-DiagnosticText $StderrTask.Result
        $ExitCode = $Process.ExitCode
    } catch {
        $Stdout = ''
        $Stderr = Protect-DiagnosticText $_.Exception.Message
        $ExitCode = $null
    } finally {
        $Process.Dispose()
    }

    $SafeName = $Name -replace '[^A-Za-z0-9_.-]', '_'
    Set-Content -LiteralPath (Join-Path $ReportDirectory "$SafeName.stdout.json") `
        -Value $Stdout -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $ReportDirectory "$SafeName.stderr.txt") `
        -Value $Stderr -Encoding UTF8

    $Data = $null
    $ParseError = $null
    if (-not [string]::IsNullOrWhiteSpace($Stdout)) {
        try {
            $Data = $Stdout | ConvertFrom-Json -ErrorAction Stop
        } catch {
            $ParseError = "Command output was not valid JSON: $($_.Exception.Message)"
        }
    } else {
        $ParseError = 'Command returned no JSON output.'
    }
    $ErrorText = if ($ParseError) { $ParseError } elseif ($Stderr) { $Stderr.Trim() } else { $null }
    return [ordered]@{
        command = "agent-eval $($Arguments -join ' ')"
        exit_code = $ExitCode
        duration_ms = [int]((Get-Date) - $Started).TotalMilliseconds
        success = ($ExitCode -eq 0 -and $null -ne $Data)
        data = $Data
        error = $ErrorText
    }
}

function Add-StepFailureIssue {
    param(
        [Parameter(Mandatory)][string]$Component,
        [Parameter(Mandatory)]$Step,
        [string]$FallbackAction = 'Inspect the saved command stdout/stderr files.'
    )
    if ($Step.success) { return }
    $Failure = $null
    $StepData = Get-DiagnosticProperty -InputObject $Step -Name 'data'
    $Failure = Get-DiagnosticProperty -InputObject $StepData -Name 'failure'
    $FailureCategory = Get-DiagnosticProperty -InputObject $Failure -Name 'category'
    $FailureSummary = Get-DiagnosticProperty -InputObject $Failure -Name 'summary'
    if (-not $FailureSummary) {
        $FailureSummary = Get-DiagnosticProperty -InputObject $Failure -Name 'title'
    }
    $FailureDetail = Get-DiagnosticProperty -InputObject $Failure -Name 'detail'
    if (-not $FailureDetail) {
        $FailureDetail = Get-DiagnosticProperty -InputObject $Failure -Name 'technical_detail'
    }
    $FailureAction = Get-DiagnosticProperty -InputObject $Failure -Name 'suggested_action'
    $StepDataError = Get-DiagnosticProperty -InputObject $StepData -Name 'error'
    $StepError = Get-DiagnosticProperty -InputObject $Step -Name 'error'
    $TechnicalDetail = Get-DiagnosticProperty -InputObject $Failure -Name 'technical_detail'
    $Category = if ($FailureCategory) {
        [string]$FailureCategory
    } else { 'command_failed' }
    $Summary = if ($FailureSummary) {
        [string]$FailureSummary
    } else { "$Component self-test failed" }
    $DetailParts = [System.Collections.Generic.List[string]]::new()
    foreach ($Part in @($FailureDetail, $TechnicalDetail, $StepDataError, $StepError)) {
        $Text = [string]$Part
        if ($Text.Trim() -and -not $DetailParts.Contains($Text.Trim())) {
            $DetailParts.Add($Text.Trim())
        }
    }
    $Detail = $DetailParts -join ' | '
    $Action = if ($FailureAction) {
        [string]$FailureAction
    } else { $FallbackAction }
    $ProtocolProbe = Get-DiagnosticProperty -InputObject $StepData -Name 'protocol_probe'
    $Evidence = [ordered]@{
        status = Get-DiagnosticProperty -InputObject $StepData -Name 'status'
        status_code = Get-DiagnosticProperty -InputObject $Failure -Name 'status_code'
        runtime_exit_code = Get-DiagnosticProperty -InputObject $StepData -Name 'runtime_exit_code'
        agent_exit_code = Get-DiagnosticProperty -InputObject $StepData -Name 'agent_exit_code'
        executable = Get-DiagnosticProperty -InputObject $StepData -Name 'executable'
        protocol_probe_status = Get-DiagnosticProperty -InputObject $ProtocolProbe -Name 'status'
    }
    $Changes = @(Get-FailureConfigurationChanges -Category $Category -Component $Component)
    Add-DiagnosticIssue -Severity error -Component $Component -Category $Category `
        -Summary $Summary -Detail $Detail -SuggestedAction $Action `
        -ConfigurationChanges $Changes -Evidence $Evidence
}

$RuntimeSpecifications = @(
    @{ Name='Python'; Variable='PYTHON_EXECUTABLE'; Default='backend/.runtime/windows/python/Scripts/python.exe'; Required=$true },
    @{ Name='Multica runtime'; Variable='MULTICA_EXECUTABLE'; Default='backend/.runtime/windows/bin/multica-eval-runtime.exe'; Required=$true },
    @{ Name='Skill-Up'; Variable='SKILLUP_EXECUTABLE'; Default='backend/.tools/windows/skill-up.exe'; Required=$true },
    @{ Name='Node'; Variable='NODE_EXECUTABLE'; Default='backend/.runtime/windows/node/node.exe'; Required=$true },
    @{ Name='npm'; Variable='NPM_EXECUTABLE'; Default='backend/.runtime/windows/node/npm.cmd'; Required=$true },
    @{ Name='Go'; Variable='GO_EXECUTABLE'; Default='backend/.runtime/windows/go/bin/go.exe'; Required=$false },
    @{ Name='Frontend modules'; Variable=''; Default='frontend/node_modules/vite/bin/vite.js'; Required=$true }
)
$RuntimeChecks = @(
    foreach ($Specification in $RuntimeSpecifications) {
        $Path = if ($Specification.Variable) {
            Get-AgentEvalConfiguredPath -ProjectRoot $ProjectRoot `
                -Name $Specification.Variable -Default $Specification.Default
        } else {
            Resolve-AgentEvalPath -ProjectRoot $ProjectRoot -Value $Specification.Default
        }
        $Exists = Test-Path -LiteralPath $Path
        if (-not $Exists) {
            Add-DiagnosticIssue `
                -Severity $(if ($Specification.Required) { 'error' } else { 'warning' }) `
                -Component 'runtime' -Category 'missing_file' `
                -Summary "$($Specification.Name) is missing" -Detail $Path `
                -SuggestedAction 'Run install_windows.ps1 with the extracted Release directory, then rerun doctor.ps1.'
        }
        [ordered]@{
            name = $Specification.Name
            path = $Path
            required = [bool]$Specification.Required
            status = if ($Exists) { 'ok' } else { 'missing' }
        }
    }
)
$PythonExecutable = [string](
    $RuntimeChecks | Where-Object { $_.name -eq 'Python' } | Select-Object -First 1
).path

$ConfigurationChecks = [System.Collections.ArrayList]::new()
if ($EnvironmentError) {
    Add-DiagnosticIssue -Severity error -Component 'configuration' -Category 'invalid_env' `
        -Summary 'Repository-root .env cannot be parsed' -Detail $EnvironmentError `
        -SuggestedAction 'Fix the reported .env line. Keep one NAME=VALUE entry per line.'
}
foreach ($Name in @('LITELLM_API_BASE', 'LITELLM_API_KEY')) {
    $Present = $EnvironmentValues.ContainsKey($Name) -and `
        -not [string]::IsNullOrWhiteSpace([string]$EnvironmentValues[$Name])
    $ConfigurationChecks.Add([ordered]@{
        name = $Name
        status = if ($Present) { 'configured' } else { 'missing' }
        sensitive = $Name -ne 'LITELLM_API_BASE'
    }) | Out-Null
    if (-not $Present) {
        Add-DiagnosticIssue -Severity error -Component 'configuration' `
            -Category 'missing_setting' -Summary "$Name is missing" `
            -SuggestedAction 'Set the value in the repository-root .env file.'
    }
}
$MasterKeyPresent = $EnvironmentValues.ContainsKey('LITELLM_MASTER_KEY') -and `
    -not [string]::IsNullOrWhiteSpace([string]$EnvironmentValues['LITELLM_MASTER_KEY'])
$ConfigurationChecks.Add([ordered]@{
    name = 'LITELLM_MASTER_KEY'
    status = if ($MasterKeyPresent) { 'configured' } else { 'missing' }
    sensitive = $true
    required_for = 'strict model/database attribution only'
}) | Out-Null
if ($Strict -and -not $MasterKeyPresent) {
    Add-DiagnosticIssue -Severity error -Component 'configuration' `
        -Category 'trace_key_not_configured' -Summary 'Strict verification requires LITELLM_MASTER_KEY' `
        -SuggestedAction 'Set an intranet LiteLLM Master Key that may call /key/generate and /key/delete.'
}

$ModelsToTest = [System.Collections.Generic.List[string]]::new()
foreach ($Item in $Model) {
    $Value = [string]$Item
    if ($Value.Trim() -and -not $ModelsToTest.Contains($Value.Trim())) {
        $ModelsToTest.Add($Value.Trim())
    }
}
if ($ModelsToTest.Count -eq 0) {
    foreach ($Name in @('LITELLM_MODEL', 'AGENT_TEST_MODEL', 'LITELLM_JUDGE_MODEL')) {
        if ($EnvironmentValues.ContainsKey($Name)) {
            $Value = [string]$EnvironmentValues[$Name]
            if ($Value.Trim() -and -not $ModelsToTest.Contains($Value.Trim())) {
                $ModelsToTest.Add($Value.Trim())
            }
        }
    }
}
if ($ModelsToTest.Count -eq 0) {
    Add-DiagnosticIssue -Severity error -Component 'configuration' -Category 'missing_model' `
        -Summary 'No model was selected for inference testing' `
        -SuggestedAction 'Set LITELLM_MODEL in .env or pass -Model.'
}

if ($PythonExecutable -and (Test-Path -LiteralPath $PythonExecutable)) {
    $Steps['doctor'] = Invoke-AgentEvalJson -Name 'doctor' -Arguments @('doctor')
    Add-StepFailureIssue -Component 'runtime-doctor' -Step $Steps['doctor']

    $Steps['model_catalog'] = Invoke-AgentEvalJson -Name 'model-catalog' `
        -Arguments @('models', '--list')
    Add-StepFailureIssue -Component 'litellm-catalog' -Step $Steps['model_catalog'] `
        -FallbackAction 'Check LITELLM_API_BASE, network routing, identity headers and LITELLM_API_KEY.'

    foreach ($SelectedModel in $ModelsToTest) {
        $Name = "model-$SelectedModel"
        $Steps[$Name] = Invoke-AgentEvalJson -Name $Name `
            -Arguments @('check-litellm', '--model', $SelectedModel, '--timeout', [string]$Timeout)
        Add-StepFailureIssue -Component "model:$SelectedModel" -Step $Steps[$Name] `
            -FallbackAction 'Confirm the exact LiteLLM deployment id, upstream quota and Chat Completions compatibility.'
    }

    if ($ProbeAllModels) {
        $Steps['all_model_probes'] = Invoke-AgentEvalJson -Name 'all-model-probes' `
            -Arguments @(
                'models', '--refresh', '--show-unavailable',
                '--workers', [string]$ModelWorkers,
                '--timeout', [string]$Timeout
            )
        Add-StepFailureIssue -Component 'all-model-probes' -Step $Steps['all_model_probes'] `
            -FallbackAction 'Inspect each unavailable_models entry for HTTP status, quota or protocol errors.'
    }

    $Steps['database'] = Invoke-AgentEvalJson -Name 'database' `
        -Arguments @('check-database')
    if (-not $Steps['database'].success -or `
        ($Steps['database'].data -and $Steps['database'].data.status -ne 'ok')) {
        Add-StepFailureIssue -Component 'database' -Step $Steps['database'] `
            -FallbackAction 'Check DATABASE_URL or DATABASE_HOST/PORT/NAME/USER/PASSWORD, SSL mode and LiteLLM_SpendLogs read permission.'
    }

    $Steps['database_schema'] = Invoke-AgentEvalJson -Name 'database-schema' `
        -Arguments @('inspect-database')
    $DatabaseAuditData = Get-DiagnosticProperty -InputObject $Steps['database_schema'] -Name 'data'
    $DatabaseAuditIssues = Get-DiagnosticProperty -InputObject $DatabaseAuditData -Name 'issues'
    if ($DatabaseAuditIssues) {
        foreach ($AuditIssue in @($DatabaseAuditIssues)) {
            $AuditSeverity = [string](Get-DiagnosticProperty -InputObject $AuditIssue -Name 'severity')
            if ($AuditSeverity -notin @('error', 'warning', 'info')) { $AuditSeverity = 'error' }
            Add-DiagnosticIssue -Severity $AuditSeverity -Component 'database-schema' `
                -Category ([string](Get-DiagnosticProperty -InputObject $AuditIssue -Name 'category')) `
                -Summary ([string](Get-DiagnosticProperty -InputObject $AuditIssue -Name 'summary')) `
                -Detail ([string](Get-DiagnosticProperty -InputObject $AuditIssue -Name 'detail')) `
                -SuggestedAction ([string](Get-DiagnosticProperty -InputObject $AuditIssue -Name 'suggested_action')) `
                -ConfigurationChanges @(Get-DiagnosticProperty -InputObject $AuditIssue -Name 'configuration_changes')
        }
    } elseif (-not $Steps['database_schema'].success) {
        Add-StepFailureIssue -Component 'database-schema' -Step $Steps['database_schema'] `
            -FallbackAction 'Compare the intranet LiteLLM database migration version with backend/config/database-schema-baseline.json.'
    }

    $Steps['agent_catalog'] = Invoke-AgentEvalJson -Name 'agent-catalog' `
        -Arguments @('agents', '--all')
    Add-StepFailureIssue -Component 'agent-catalog' -Step $Steps['agent_catalog']

    $AgentsToTest = [System.Collections.Generic.List[string]]::new()
    foreach ($Item in $Agent) {
        $Value = [string]$Item
        if ($Value.Trim() -and -not $AgentsToTest.Contains($Value.Trim().ToLowerInvariant())) {
            $AgentsToTest.Add($Value.Trim().ToLowerInvariant())
        }
    }
    if ($AgentsToTest.Count -eq 0 -and $Steps['agent_catalog'].data) {
        foreach ($Row in @($Steps['agent_catalog'].data)) {
            if ($Row.availability -and $Row.availability.available -eq $true) {
                $Value = [string]$Row.agent
                if ($Value -and -not $AgentsToTest.Contains($Value)) { $AgentsToTest.Add($Value) }
            }
        }
    }
    if ($AgentsToTest.Count -eq 0) {
        Add-DiagnosticIssue -Severity error -Component 'agents' -Category 'no_agent_available' `
            -Summary 'No runnable Agent was detected' `
            -SuggestedAction 'Install an Agent or configure its absolute executable path in AGENT_PATHS_JSON.'
    } elseif ($ModelsToTest.Count -gt 0) {
        $AgentModel = $ModelsToTest[0]
        foreach ($SelectedAgent in $AgentsToTest) {
            $Name = "agent-$SelectedAgent-basic"
            $Steps[$Name] = Invoke-AgentEvalJson -Name $Name -Arguments @(
                'check-agent', '--agent', $SelectedAgent, '--model', $AgentModel,
                '--prompt', '只回复 AGENT_SELF_TEST_OK', '--timeout', [string]$Timeout,
                '--no-database-verify'
            )
            $Connected = $Steps[$Name].success -and $Steps[$Name].data -and `
                $Steps[$Name].data.status -eq 'connected'
            if (-not $Connected) {
                Add-StepFailureIssue -Component "agent:$SelectedAgent" -Step $Steps[$Name] `
                    -FallbackAction 'Check the executable path, Agent version, model protocol and the saved stderr output.'
            }

            if ($Strict) {
                $StrictName = "agent-$SelectedAgent-strict"
                if ($MasterKeyPresent -and $Steps['database'].data -and `
                    $Steps['database'].data.status -eq 'ok') {
                    $Steps[$StrictName] = Invoke-AgentEvalJson -Name $StrictName -Arguments @(
                        'check-agent', '--agent', $SelectedAgent, '--model', $AgentModel,
                        '--prompt', '只回复 AGENT_STRICT_SELF_TEST_OK',
                        '--timeout', [string]$Timeout, '--database-verify'
                    )
                    $StrictConnected = $Steps[$StrictName].success -and `
                        $Steps[$StrictName].data -and $Steps[$StrictName].data.status -eq 'connected'
                    if (-not $StrictConnected) {
                        Add-StepFailureIssue -Component "agent-strict:$SelectedAgent" `
                            -Step $Steps[$StrictName] `
                            -FallbackAction 'Check Master Key permissions, /key/generate, /key/delete and LiteLLM_SpendLogs attribution.'
                    }
                } else {
                    $Steps[$StrictName] = [ordered]@{
                        command = 'strict check-agent'
                        exit_code = $null
                        duration_ms = 0
                        success = $false
                        data = $null
                        error = 'Skipped because database or LITELLM_MASTER_KEY is unavailable.'
                    }
                }
            }
        }
    }
} else {
    Add-DiagnosticIssue -Severity error -Component 'self-test' -Category 'python_unavailable' `
        -Summary 'Cannot run application-level checks without the project Python runtime' `
        -SuggestedAction 'Run install_windows.ps1 with the extracted Release directory.'
}

$ErrorCount = @($Issues | Where-Object { $_.severity -eq 'error' }).Count
$WarningCount = @($Issues | Where-Object { $_.severity -eq 'warning' }).Count
$OverallStatus = if ($ErrorCount -eq 0) { 'ready' } else { 'not_ready' }
$Report = [ordered]@{
    schema_version = 1
    generated_at = (Get-Date).ToUniversalTime().ToString('o')
    overall_status = $OverallStatus
    project_root = $ProjectRoot
    host = $env:COMPUTERNAME
    parameters = [ordered]@{
        requested_agents = @($Agent)
        tested_models = @($ModelsToTest)
        timeout_seconds = $Timeout
        probe_all_models = [bool]$ProbeAllModels
        strict_verification = [bool]$Strict
    }
    summary = [ordered]@{
        errors = $ErrorCount
        warnings = $WarningCount
        report_directory = $ReportDirectory
    }
    runtime = $RuntimeChecks
    configuration = @($ConfigurationChecks)
    issues = @($Issues)
    steps = $Steps
}

$JsonPath = Join-Path $ReportDirectory 'self-test-report.json'
$TextPath = Join-Path $ReportDirectory 'self-test-summary.txt'
$Report | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $JsonPath -Encoding UTF8

$Lines = [System.Collections.Generic.List[string]]::new()
$Lines.Add("Agent Eval intranet self-test: $OverallStatus")
$Lines.Add("Generated: $($Report.generated_at)")
$Lines.Add("Project: $ProjectRoot")
$Lines.Add("Errors: $ErrorCount; Warnings: $WarningCount")
$Lines.Add('')
$Lines.Add('Runtime:')
foreach ($Item in $RuntimeChecks) {
    $Lines.Add("  [$($Item.status.ToUpperInvariant())] $($Item.name): $($Item.path)")
}
$Lines.Add('')
$Lines.Add('Issues:')
if ($Issues.Count -eq 0) {
    $Lines.Add('  None. The requested checks passed.')
} else {
    foreach ($Issue in $Issues) {
        $Lines.Add("  [$($Issue.severity.ToUpperInvariant())] $($Issue.component): $($Issue.summary)")
        if ($Issue.detail) { $Lines.Add("    Detail: $($Issue.detail)") }
        if ($Issue.suggested_action) { $Lines.Add("    Action: $($Issue.suggested_action)") }
        foreach ($Change in @($Issue.configuration_changes)) {
            if ($Change) { $Lines.Add("    Change: $Change") }
        }
        if ($Issue.evidence) {
            $Lines.Add("    Evidence: $($Issue.evidence | ConvertTo-Json -Compress -Depth 5)")
        }
    }
}
$Lines.Add('')
$Lines.Add("Full JSON report: $JsonPath")
$Lines | Set-Content -LiteralPath $TextPath -Encoding UTF8

Write-Host ''
Write-Host "Agent Eval self-test finished: $OverallStatus" `
    -ForegroundColor $(if ($OverallStatus -eq 'ready') { 'Green' } else { 'Red' })
Write-Host "  Errors:  $ErrorCount"
Write-Host "  Warnings: $WarningCount"
Write-Host "  Summary:  $TextPath"
Write-Host "  Details:  $JsonPath"

if ($OverallStatus -ne 'ready' -and -not $NoFailExit) { exit 1 }
