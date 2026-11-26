$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
function RunChecked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}
try {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            throw 'Install uv from https://docs.astral.sh/uv/getting-started/installation/ and double-click setup-ikaros.cmd again.'
        }
        Write-Host 'Installing uv using Windows Package Manager. Approve prompts if asked.'
        RunChecked 'winget' @('install','--id','astral-sh.uv','--exact','--source','winget')
        $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User') + ';' + $env:Path
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            throw 'uv installation finished. Close this window and double-click setup-ikaros.cmd again so Windows reloads PATH.'
        }
    }
    RunChecked 'uv' @('sync','--locked','--python','3.12')
    RunChecked 'uv' @('run','--no-sync','python','-m','run.prepare_daedalus')
    Write-Host 'Local WIG20 checks passed. You can now prepare Daedalus.' -ForegroundColor Green
    exit 0
} catch {
    Write-Host "Setup incomplete: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
