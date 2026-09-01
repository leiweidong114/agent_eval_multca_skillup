param(
    [Parameter(Mandatory=$true)][string]$Server,
    [int]$LiteLLMPort = 4000,
    [int]$DatabasePort = 5432,
    [Parameter(Mandatory=$true)][string]$Model,
    [Parameter(Mandatory=$true)][string]$LiteLLMKey,
    [Parameter(Mandatory=$true)][string]$DatabasePassword,
    [string]$DatabaseName = "litellm",
    [string]$DatabaseUser = "agent_eval_reader",
    [string]$LiteLLMMasterKey = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$target = Join-Path $projectRoot "backend\config\local.yaml"
$escapedModel = $Model.Replace("'", "''")
$escapedKey = $LiteLLMKey.Replace("'", "''")
$escapedPassword = $DatabasePassword.Replace("'", "''")
$escapedMaster = $LiteLLMMasterKey.Replace("'", "''")
$content = @"
default_profile: intranet_default
profiles:
  intranet_default:
    model: '$escapedModel'
    api_base: 'http://${Server}:${LiteLLMPort}/v1'
    api_key_env: LITELLM_API_KEY
database:
  enabled: true
  host: '$Server'
  port: $DatabasePort
  name: '$DatabaseName'
  user: '$DatabaseUser'
  password_env: LITELLM_DATABASE_PASSWORD
secrets:
  LITELLM_API_KEY: '$escapedKey'
  LITELLM_DATABASE_PASSWORD: '$escapedPassword'
  LITELLM_MASTER_KEY: '$escapedMaster'
agents: {}
"@
[System.IO.File]::WriteAllText($target, $content, [System.Text.UTF8Encoding]::new($false))
Write-Host "Wrote $target"
Write-Host "Add Agent executable paths under 'agents:' when installations are ready."
