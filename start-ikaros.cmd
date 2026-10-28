@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\start-ikaros.ps1"
if errorlevel 1 echo Startup failed; see messages above.
pause
