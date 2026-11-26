@echo off
cd /d "%~dp0"
if not exist "%~dp0Ikaros\Scripts\python.exe" (
    echo Ikaros environment not found. Run setup-ikaros.cmd first.
    exit /b 1
)
"%~dp0Ikaros\Scripts\python.exe" -m run.ikaros %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo Ikaros command failed, inspect log above.
exit /b %RC%
