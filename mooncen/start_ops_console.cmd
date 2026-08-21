@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_ops_console.ps1" %*
exit /b %ERRORLEVEL%
