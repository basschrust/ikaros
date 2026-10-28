$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
foreach ($file in @('compose.kafka.yaml','run/kafka_sink.py','run/kafka_contract_sink.py','run/ikaros_scheduler.py','run/backfill_gpw_snowflake.py')) {
    if (-not (Test-Path $file)) { throw "Missing $file; install the scheduler and ingestion modules first." }
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'uv not found in PATH. Run setup first.' }
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker CLI not found.' }
& docker compose -f compose.kafka.yaml up -d
if ($LASTEXITCODE -ne 0) { throw 'Cannot start Kafka. Start Docker Desktop first.' }
# Only direct, provider-supported FX pairs. Extend after seeding CURRENCY_PAIRS in Snowflake.
$FX_SYMBOLS = @('EURPLN=X')
# Change this flag to $false to disable short recent-price catch-up at startup.
$RUN_RECENT_CATCHUP = $true
$endDate = (Get-Date).AddDays(-1).ToString('yyyy-MM-dd')
$startDate = (Get-Date).AddDays(-8).ToString('yyyy-MM-dd')
function Launch([string]$Title, [string]$Command) {
    # Single-quoted paths inside command strings are escaped for PowerShell.
    $cwd = $root.Replace("'", "''")
    $script = "Set-Location '$cwd'; `$env:IKAROS_KAFKA_BROKERS='127.0.0.1:9092'; `$Host.UI.RawUI.WindowTitle='$Title'; $Command"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
    Start-Process powershell.exe -WorkingDirectory $root -ArgumentList @('-NoExit','-NoProfile','-EncodedCommand', $encoded) | Out-Null
    Write-Host "Started $Title"
}
# Initial shells are independent; do not launch multiple copies of this file.
Launch 'Ikaros FX sink' 'uv run --no-sync --extra kafka --extra snowflake python -m run.kafka_sink --dataset FX'
Launch 'Ikaros GPW sink' 'uv run --no-sync --extra kafka --extra snowflake python -m run.kafka_sink --dataset GPW'
Launch 'Ikaros derivatives discovery sink' 'uv run --no-sync --extra kafka --extra snowflake python -m run.kafka_contract_sink'
$fxFlags = ($FX_SYMBOLS | ForEach-Object { '"' + $_ + '"' }) -join ' '
Launch 'Ikaros scheduler' "uv run --no-sync --extra kafka --extra snowflake python -m run.ikaros_scheduler --fx-symbols $fxFlags --write"
if ($RUN_RECENT_CATCHUP) {
    Launch 'Ikaros GPW recent catchup' "uv run --no-sync --extra snowflake python -m run.backfill_gpw_snowflake --start $startDate --end $endDate --write --continue-on-error"
}
Write-Host 'Ikaros processes launched. Keep the windows and Docker Desktop running. Do not launch twice.' -ForegroundColor Green
