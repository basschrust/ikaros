param([switch]$EnvironmentOnly, [switch]$Developer)
# The default local action is intentionally the same as setup: it is repeatable.
& (Join-Path $PSScriptRoot 'setup-ikaros.ps1') -EnvironmentOnly:$EnvironmentOnly -Developer:$Developer
exit $LASTEXITCODE
