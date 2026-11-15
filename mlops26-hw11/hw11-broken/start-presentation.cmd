@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
uv run --no-sync python -m scripts.presentation_server --open
if errorlevel 1 pause
