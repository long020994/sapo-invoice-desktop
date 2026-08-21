@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%CD%\.vendor-v26;%CD%\.venv-build\Lib\site-packages"
start "Sapo Invoice Desktop" "%CD%\.python314-nuget\tools\python.exe" "%CD%\run_app.pyw"
