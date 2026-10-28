@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\setup-ikaros.ps1"
if errorlevel 1 echo Setup failed; see messages above.
pause
