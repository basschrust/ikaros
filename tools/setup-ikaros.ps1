param([switch]$EnvironmentOnly, [switch]$Developer)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:UV_PROJECT_ENVIRONMENT = 'Ikaros'
$python = Join-Path $root 'Ikaros\Scripts\python.exe'

function RunChecked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User') + ';' + $env:Path
    $env:Path += ';' + (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links') + ';' + (Join-Path $HOME '.local\bin')
}
function Has-Distribution([string]$Pattern) {
    foreach ($directory in @('Ikaros', '.venv')) {
        $site = Join-Path $root "$directory\Lib\site-packages"
        if (Test-Path -LiteralPath $site) {
            if (Get-ChildItem -LiteralPath $site -Filter $Pattern -ErrorAction SilentlyContinue | Select-Object -First 1) {
                return $true
            }
        }
    }
    return $false
}
try {
    Refresh-Path
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            throw 'Install uv from https://docs.astral.sh/uv/getting-started/installation/ and rerun setup-ikaros.cmd.'
        }
        Write-Host 'Installing uv using Windows Package Manager. Approve prompts if asked.'
        RunChecked 'winget' @('install','--id','astral-sh.uv','--exact','--source','winget')
        Refresh-Path
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            throw 'uv installation finished but uv is not on PATH. Restart Windows Terminal or PyCharm and rerun setup.'
        }
    }
    # Keep any optional connector extras already present in either environment.
    # New local installations do not install cloud connectors by default.
    $syncArgs = @('sync','--locked','--python','3.12','--inexact')
    if ($Developer) {
        $syncArgs += '--all-extras'
    } else {
        if (Has-Distribution 'snowflake_connector_python-*.dist-info') { $syncArgs += @('--extra','snowflake') }
        if (Has-Distribution 'confluent_kafka-*.dist-info') { $syncArgs += @('--extra','kafka') }
    }
    Write-Host 'Preparing the Ikaros Python environment...'
    if (-not (Test-Path -LiteralPath $python)) {
        RunChecked 'uv' @('venv','--python','3.12','--prompt','Ikaros','Ikaros')
    }
    RunChecked 'uv' $syncArgs
    RunChecked $python @('-c','import sys; print(sys.executable)')
    Write-Host 'Ikaros environment ready.' -ForegroundColor Green
    if (Test-Path -LiteralPath (Join-Path $root '.venv')) {
        Write-Host 'Old .venv retained. Select Ikaros\Scripts\python.exe in PyCharm before removing it.'
    }
    if ($EnvironmentOnly) { exit 0 }
    RunChecked $python @('-m','run.prepare_daedalus')
    Write-Host 'Local WIG20 checks passed. You can now prepare Daedalus.' -ForegroundColor Green
    exit 0
} catch {
    Write-Host "Setup incomplete: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
