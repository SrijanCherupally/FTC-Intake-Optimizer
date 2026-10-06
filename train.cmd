@echo off
setlocal
set "FUNNEL_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%FUNNEL_PY%" (
  "%FUNNEL_PY%" "%~dp0train.py" %*
) else (
  py -3 "%~dp0train.py" %*
)
exit /b %errorlevel%
