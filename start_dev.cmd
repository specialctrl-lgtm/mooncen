@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "MOONCEN_STATUS_MODE=0"
set "MOONCEN_STOP_MODE=0"
set "MOONCEN_API_PORT=8001"
set "MOONCEN_FRONTEND_PORT=5174"
set "MOONCEN_NEXT_PORT_ARG="
set "MOONCEN_PS_ARGS="
setlocal EnableDelayedExpansion
for %%A in (%*) do (
  set "MOONCEN_ARG=%%~A"
  set "MOONCEN_PS_ARG=!MOONCEN_ARG!"
  set "MOONCEN_APPEND_ARG=1"
  if defined MOONCEN_NEXT_PORT_ARG (
    if /I "!MOONCEN_NEXT_PORT_ARG!"=="api" set "MOONCEN_API_PORT=!MOONCEN_ARG!"
    if /I "!MOONCEN_NEXT_PORT_ARG!"=="frontend" set "MOONCEN_FRONTEND_PORT=!MOONCEN_ARG!"
    set "MOONCEN_NEXT_PORT_ARG="
  ) else (
    if /I "!MOONCEN_ARG!"=="-Status" set "MOONCEN_STATUS_MODE=1"
    if /I "!MOONCEN_ARG!"=="/Status" set "MOONCEN_STATUS_MODE=1"
    if /I "!MOONCEN_ARG!"=="-Stop" set "MOONCEN_STOP_MODE=1"
    if /I "!MOONCEN_ARG!"=="/Stop" set "MOONCEN_STOP_MODE=1"
    if /I "!MOONCEN_ARG!"=="-ApiPort" set "MOONCEN_NEXT_PORT_ARG=api"
    if /I "!MOONCEN_ARG!"=="/ApiPort" set "MOONCEN_NEXT_PORT_ARG=api"
    if /I "!MOONCEN_ARG:~0,9!"=="-ApiPort:" set "MOONCEN_API_PORT=!MOONCEN_ARG:~9!"
    if /I "!MOONCEN_ARG:~0,9!"=="/ApiPort:" set "MOONCEN_API_PORT=!MOONCEN_ARG:~9!"
    if /I "!MOONCEN_ARG!"=="-FrontendPort" set "MOONCEN_NEXT_PORT_ARG=frontend"
    if /I "!MOONCEN_ARG!"=="/FrontendPort" set "MOONCEN_NEXT_PORT_ARG=frontend"
    if /I "!MOONCEN_ARG:~0,14!"=="-FrontendPort:" set "MOONCEN_FRONTEND_PORT=!MOONCEN_ARG:~14!"
    if /I "!MOONCEN_ARG:~0,14!"=="/FrontendPort:" set "MOONCEN_FRONTEND_PORT=!MOONCEN_ARG:~14!"
  )

  rem PowerShell scripts only bind switches reliably with '-' prefixes.
  if /I "!MOONCEN_ARG!"=="/Status" set "MOONCEN_PS_ARG=-Status"
  if /I "!MOONCEN_ARG!"=="/Stop" set "MOONCEN_PS_ARG=-Stop"
  if /I "!MOONCEN_ARG!"=="/Restart" set "MOONCEN_PS_ARG=-Restart"
  if /I "!MOONCEN_ARG!"=="/FrontendOnly" set "MOONCEN_PS_ARG=-FrontendOnly"
  if /I "!MOONCEN_ARG!"=="/Open" set "MOONCEN_PS_ARG=-Open"
  if /I "!MOONCEN_ARG!"=="/ApiPort" set "MOONCEN_PS_ARG=-ApiPort"
  if /I "!MOONCEN_ARG!"=="/FrontendPort" set "MOONCEN_PS_ARG=-FrontendPort"
  if /I "!MOONCEN_ARG!"=="/StartupTimeoutSec" set "MOONCEN_PS_ARG=-StartupTimeoutSec"
  if /I "!MOONCEN_ARG:~0,9!"=="/ApiPort:" (
    set "MOONCEN_PS_ARGS=!MOONCEN_PS_ARGS! -ApiPort !MOONCEN_ARG:~9!"
    set "MOONCEN_APPEND_ARG=0"
  )
  if /I "!MOONCEN_ARG:~0,14!"=="/FrontendPort:" (
    set "MOONCEN_PS_ARGS=!MOONCEN_PS_ARGS! -FrontendPort !MOONCEN_ARG:~14!"
    set "MOONCEN_APPEND_ARG=0"
  )
  if /I "!MOONCEN_ARG:~0,19!"=="/StartupTimeoutSec:" (
    set "MOONCEN_PS_ARGS=!MOONCEN_PS_ARGS! -StartupTimeoutSec !MOONCEN_ARG:~19!"
    set "MOONCEN_APPEND_ARG=0"
  )
  if "!MOONCEN_APPEND_ARG!"=="1" set "MOONCEN_PS_ARGS=!MOONCEN_PS_ARGS! !MOONCEN_PS_ARG!"
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_dev.ps1" !MOONCEN_PS_ARGS!
set "MOONCEN_EXIT=%ERRORLEVEL%"
if "%MOONCEN_EXIT%"=="0" (
  if "%MOONCEN_STATUS_MODE%"=="0" (
    echo.
    if "%MOONCEN_STOP_MODE%"=="1" (
      echo MoonCen dev server stopped.
    ) else (
      echo MoonCen dev server is ready.
      echo   Frontend: http://127.0.0.1:%MOONCEN_FRONTEND_PORT%
      echo   API:      http://127.0.0.1:%MOONCEN_API_PORT%
      echo.
      echo Check status with:
      echo   powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_dev.ps1" -ApiPort %MOONCEN_API_PORT% -FrontendPort %MOONCEN_FRONTEND_PORT% -Status
    )
  )
) else (
  echo.
  echo MoonCen dev server failed. Check status with:
  echo   powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_dev.ps1" -ApiPort %MOONCEN_API_PORT% -FrontendPort %MOONCEN_FRONTEND_PORT% -Status
  echo Logs:
  echo   "%~dp0logs"
)
exit /b %MOONCEN_EXIT%
