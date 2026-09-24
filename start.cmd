@echo off
setlocal
set PY=%MIMO_PYTHON%
if "%PY%"=="" set PY=python
cd /d "%~dp0"
"%PY%" -m mimo_bridge doctor
echo.
"%PY%" -m mimo_bridge serve --port 8787
