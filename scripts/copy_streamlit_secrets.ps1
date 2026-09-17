$ErrorActionPreference = 'Stop'

$values = @{}
foreach ($line in Get-Content -LiteralPath '.env' -Encoding UTF8) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
        $values[$matches[1]] = $matches[2]
    }
}

$required = @(
    'ARK_API_KEY',
    'ARK_BASE_URL',
    'ARK_MODEL',
    'FEISHU_APP_ID',
    'FEISHU_APP_SECRET',
    'FEISHU_BITABLE_APP_TOKEN',
    'FEISHU_BITABLE_TABLE_ID',
    'FEISHU_STAGE_RULES_TABLE_ID'
)
$missing = @($required | Where-Object { -not $values.ContainsKey($_) -or [string]::IsNullOrWhiteSpace($values[$_]) })
if ($missing.Count -gt 0) {
    throw "Missing required deployment variables: $($missing -join ', ')"
}

$publicSaveEnabled = $(if ($values['FEISHU_PUBLIC_SAVE_ENABLED']) { $values['FEISHU_PUBLIC_SAVE_ENABLED'] } else { 'false' })
if ($publicSaveEnabled -match '^(?i:true|1|yes|on)$' -and [string]::IsNullOrWhiteSpace($values['DEMO_WRITE_CODE'])) {
    throw 'DEMO_WRITE_CODE is required when FEISHU_PUBLIC_SAVE_ENABLED is true'
}

$deployment = [ordered]@{
    MOCK_MODE = 'false'
    ARK_API_KEY = $values['ARK_API_KEY']
    ARK_BASE_URL = $values['ARK_BASE_URL']
    ARK_MODEL = $values['ARK_MODEL']
    ARK_STRUCTURED_MODE = $(if ($values['ARK_STRUCTURED_MODE']) { $values['ARK_STRUCTURED_MODE'] } else { 'tool_call' })
    ARK_TIMEOUT_SECONDS = $(if ($values['ARK_TIMEOUT_SECONDS']) { $values['ARK_TIMEOUT_SECONDS'] } else { '30' })
    ARK_MAX_RETRIES = $(if ($values['ARK_MAX_RETRIES']) { $values['ARK_MAX_RETRIES'] } else { '2' })
    ARK_FALLBACK_TO_MOCK = 'true'
    FEISHU_SYNC_ENABLED = 'true'
    FEISHU_APP_ID = $values['FEISHU_APP_ID']
    FEISHU_APP_SECRET = $values['FEISHU_APP_SECRET']
    FEISHU_BITABLE_APP_TOKEN = $values['FEISHU_BITABLE_APP_TOKEN']
    FEISHU_BITABLE_TABLE_ID = $values['FEISHU_BITABLE_TABLE_ID']
    FEISHU_STAGE_RULES_TABLE_ID = $values['FEISHU_STAGE_RULES_TABLE_ID']
    FEISHU_EVAL_TABLE_ID = $values['FEISHU_EVAL_TABLE_ID']
    FEISHU_TIMEOUT_SECONDS = $(if ($values['FEISHU_TIMEOUT_SECONDS']) { $values['FEISHU_TIMEOUT_SECONDS'] } else { '20' })
    FEISHU_MAX_RETRIES = $(if ($values['FEISHU_MAX_RETRIES']) { $values['FEISHU_MAX_RETRIES'] } else { '2' })
    PUBLIC_DEMO_MODE = 'true'
    ANALYSIS_RATE_LIMIT = '6'
    ANALYSIS_RATE_WINDOW_SECONDS = '600'
    FEISHU_PUBLIC_SAVE_ENABLED = $publicSaveEnabled
    DEMO_WRITE_CODE = $values['DEMO_WRITE_CODE']
}

$lines = foreach ($entry in $deployment.GetEnumerator()) {
    $escaped = ([string]$entry.Value).Replace('\', '\\').Replace('"', '\"')
    '{0} = "{1}"' -f $entry.Key, $escaped
}
Set-Clipboard -Value ($lines -join "`n")
Write-Output 'deployment_secrets_ready=true'
