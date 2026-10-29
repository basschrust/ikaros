@echo off
cd /d "%~dp0"
uv run --no-sync python -m run.ikaros %*
if errorlevel 1 echo Ikaros command failed, inspect log above.
