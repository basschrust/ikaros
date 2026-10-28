$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
function RunChecked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'uv is not installed or not in PATH.' }
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker CLI not found; install/start Docker Desktop.' }
foreach ($file in @('compose.kafka.yaml','config/database.yaml','run/kafka_inbound_topics.py','run/kafka_discovery_topic.py','run/ikaros_scheduler.py')) {
    if (-not (Test-Path $file)) { throw "Missing $file. Apply the Ikaros scheduler extension first." }
}
RunChecked 'uv' @('sync','--python','3.12','--extra','kafka','--extra','snowflake')
RunChecked 'docker' @('info','--format','{{.ServerVersion}}')
RunChecked 'docker' @('compose','-f','compose.kafka.yaml','up','-d')
RunChecked 'uv' @('run','--extra','kafka','python','-m','run.kafka_inbound_topics')
RunChecked 'uv' @('run','--extra','kafka','python','-m','run.kafka_discovery_topic')
Write-Host 'Ikaros setup OK. Seed instrument reference data if this is a new Snowflake database, then use start-ikaros.cmd.' -ForegroundColor Green
