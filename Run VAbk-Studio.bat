@echo off
setlocal enableextensions

rem ============================================================================
rem  Run VAbk Studio from source (self-healing dev launcher).
rem
rem  On first run this creates a .venv with uv (Python 3.12, auto-downloaded if
rem  missing), installs requirements.txt, then launches run.py. On later runs it
rem  skips straight to launching. Location-independent: double-click from
rem  anywhere. Pass-through args work too, e.g.  Run VAbk-Studio.bat --smoke
rem ============================================================================

rem Work from this script's own folder, regardless of how it was launched.
cd /d "%~dp0"

rem --- Locate uv: prefer PATH, fall back to the default per-user install dir ---
set "UV="
for /f "delims=" %%i in ('where uv 2^>nul') do if not defined UV set "UV=%%i"
if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"

if not defined UV (
  echo [ERROR] Could not find 'uv' on PATH or in %%USERPROFILE%%\.local\bin.
  echo         Install it, then re-run this launcher:
  echo             powershell -c "irm https://astral.sh/uv/install.ps1 ^| iex"
  echo         More info: https://docs.astral.sh/uv/
  echo.
  pause
  exit /b 1
)

set "PYEXE=.venv\Scripts\python.exe"

rem --- First run (or a wiped .venv): create the env and install deps ----------
if not exist "%PYEXE%" (
  echo [setup] Creating virtual environment ^(Python 3.12^)...
  "%UV%" venv --python 3.12 .venv
  if errorlevel 1 goto :fail

  echo [setup] Installing dependencies from requirements.txt...
  "%UV%" pip install --python "%PYEXE%" -r requirements.txt
  if errorlevel 1 goto :fail
)

rem --- Launch ----------------------------------------------------------------
echo [run] Launching VAbk Studio...
"%PYEXE%" run.py %*
set "RC=%errorlevel%"

if not "%RC%"=="0" (
  echo.
  echo [ERROR] VAbk Studio exited with code %RC%.
  pause
)
exit /b %RC%

:fail
echo.
echo [ERROR] Setup failed - see the messages above.
pause
exit /b 1
