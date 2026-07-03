@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_local.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if "%EXIT_CODE%"=="0" (
  echo restart_local.ps1 finished successfully. Keep this window open if you want to see the startup result.
) else (
  echo restart_local.ps1 failed with exit code %EXIT_CODE%.
)
echo.
pause
exit /b %EXIT_CODE%
