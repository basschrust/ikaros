@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\setup-ikaros.ps1" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo Local Ikaros setup incomplete. See messages above.
pause
exit /b %RC%
