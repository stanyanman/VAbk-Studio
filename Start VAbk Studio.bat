@echo off
rem One-click launcher for VAbk Studio on Windows.
rem First run creates an isolated .venv and installs dependencies; later runs are instant.
setlocal
cd /d "%~dp0"
set "VENV_PY=.venv\Scripts\python.exe"
set "VENV_PYW=.venv\Scripts\pythonw.exe"

if exist "%VENV_PY%" goto run

echo === First run: setting up VAbk Studio ===
where uv      >nul 2>nul && goto venv_uv
where py      >nul 2>nul && goto venv_py
where python  >nul 2>nul && goto venv_python
echo.
echo No 'uv', 'py', or 'python' found on PATH.
echo Install Python 3.12 ^(https://www.python.org^) or uv ^(https://docs.astral.sh/uv/^) and retry.
pause
exit /b 1

:venv_uv
uv venv --python 3.12 .venv || goto fail
uv pip install --python "%VENV_PY%" -r requirements.txt || goto fail
goto run

:venv_py
py -3.12 -m venv .venv || goto fail
"%VENV_PY%" -m pip install --upgrade pip
"%VENV_PY%" -m pip install -r requirements.txt || goto fail
goto run

:venv_python
python -m venv .venv || goto fail
"%VENV_PY%" -m pip install --upgrade pip
"%VENV_PY%" -m pip install -r requirements.txt || goto fail
goto run

:run
rem Launch the GUI with the windowless interpreter (pythonw), detached, so this
rem console can close without killing the app. (Falls back to python.exe if needed.)
if exist "%VENV_PYW%" (
    start "VAbk Studio" /D "%~dp0" "%VENV_PYW%" run.py %*
) else (
    start "VAbk Studio" /D "%~dp0" "%VENV_PY%" run.py %*
)
exit /b 0

:fail
echo.
echo Setup failed. See the messages above.
pause
exit /b 1
