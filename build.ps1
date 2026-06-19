# Build VAbk Studio into dist\VAbkStudio.exe in one shot.
#
#   Usage:  .\build.ps1
#
# Optional: most users just run from source (see "Start VAbk Studio.bat" / README).
# Requires Python 3.12 (or uv) on PATH. Creates an isolated .venv, installs the
# GUI dependencies from requirements.txt, and runs PyInstaller. The app itself
# does NOT bundle Abogen or ffmpeg, so the build stays small (~38 MB).
$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$venvPy = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

function Test-Cmd($name) {
    return $null -ne (Get-Command $name -ErrorAction SilentlyContinue)
}

if (-not (Test-Path $venvPy)) {
    if (Test-Cmd 'uv') {
        Write-Host '==> Creating .venv with uv (Python 3.12)...' -ForegroundColor Cyan
        uv venv --python 3.12 .venv
    } elseif (Test-Cmd 'py') {
        Write-Host '==> Creating .venv with the Python launcher (3.12)...' -ForegroundColor Cyan
        py -3.12 -m venv .venv
    } elseif (Test-Cmd 'python') {
        Write-Host '==> Creating .venv with python...' -ForegroundColor Cyan
        python -m venv .venv
    } else {
        throw 'No uv / py / python found on PATH. Install Python 3.12 or uv first.'
    }
}

Write-Host '==> Installing dependencies (requirements.txt + PyInstaller)...' -ForegroundColor Cyan
if (Test-Cmd 'uv') {
    uv pip install --python $venvPy -r requirements.txt pyinstaller==6.21.0
} else {
    & $venvPy -m pip install --upgrade pip
    & $venvPy -m pip install -r requirements.txt pyinstaller==6.21.0
}

Write-Host '==> Building with PyInstaller...' -ForegroundColor Cyan
& $venvPy -m PyInstaller build\VAbkStudio.spec --noconfirm --distpath dist --workpath build\work

$exe = Join-Path $PSScriptRoot 'dist\VAbkStudio.exe'
if (Test-Path $exe) {
    Write-Host ("==> Done. Built: {0}" -f $exe) -ForegroundColor Green
} else {
    throw 'Build finished but dist\VAbkStudio.exe was not found.'
}
