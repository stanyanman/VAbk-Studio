@echo off
rem ============================================================
rem  VAbk Studio - one-click launcher (Windows). Self-contained and
rem  location-independent: always uses the .venv INSIDE this folder
rem  (%~dp0); rebuilds it if missing or built on another machine.
rem  Launches python.exe hidden via VBScript (no pythonw, no console).
rem ============================================================
setlocal
cd /d "%~dp0"
set "APP_DIR=%~dp0"
set "VENV_DIR=%APP_DIR%.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "VENV_CFG=%VENV_DIR%\pyvenv.cfg"

rem --- Validate the venv for THIS machine; rebuild if its base is gone.
if not exist "%VENV_PY%"  goto setup
if not exist "%VENV_CFG%" goto rebuild
set "VENV_HOME="
for /f "tokens=1,* delims== " %%A in ('findstr /b /i /c:"home" "%VENV_CFG%"') do set "VENV_HOME=%%B"
if not defined VENV_HOME goto rebuild
if not exist "%VENV_HOME%\python.exe" goto rebuild
rem Base interpreter present; confirm the GUI deps import too. A half-finished pip
rem install would otherwise pass and then fail silently at launch. (Safe to run
rem python.exe now -- base is confirmed, so no pythonw "No Python" popup.)
"%VENV_PY%" -c "import PyQt6, requests" >nul 2>nul || goto rebuild
goto run

:rebuild
echo === The existing .venv is not valid here - rebuilding it ===
rmdir /s /q "%VENV_DIR%" 2>nul

:setup
echo === Setting up VAbk Studio (one-time) ===
where uv      >nul 2>nul && goto setup_uv
where py      >nul 2>nul && goto setup_py
where python  >nul 2>nul && goto setup_python
echo.
echo No uv, py, or python found on PATH.
echo Install Python 3.12 ^(https://www.python.org^) or uv ^(https://docs.astral.sh/uv/^) and retry.
pause
exit /b 1

:setup_uv
uv venv --python 3.12 "%VENV_DIR%" || goto fail
uv pip install --python "%VENV_PY%" -r "%APP_DIR%requirements.txt" || goto fail
goto run

:setup_py
py -3.12 -m venv "%VENV_DIR%" || goto fail
"%VENV_PY%" -m pip install --upgrade pip
"%VENV_PY%" -m pip install -r "%APP_DIR%requirements.txt" || goto fail
goto run

:setup_python
python -m venv "%VENV_DIR%" || goto fail
"%VENV_PY%" -m pip install --upgrade pip
"%VENV_PY%" -m pip install -r "%APP_DIR%requirements.txt" || goto fail
goto run

:run
rem Launch windowless: python.exe hidden via VBScript (style 0).
set "LAUNCH_VBS=%TEMP%\vabk_studio_launch.vbs"
>"%LAUNCH_VBS%"  echo Set sh = CreateObject("WScript.Shell")
>>"%LAUNCH_VBS%" echo sh.CurrentDirectory = "%APP_DIR%"
>>"%LAUNCH_VBS%" echo sh.Run Chr(34) ^& "%VENV_PY%" ^& Chr(34) ^& " run.py %*", 0, False
wscript //nologo "%LAUNCH_VBS%"
del "%LAUNCH_VBS%" >nul 2>nul
exit /b 0

:fail
echo.
echo Setup failed. See the messages above.
pause
exit /b 1
