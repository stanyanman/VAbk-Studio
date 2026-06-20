# PyInstaller spec for VAbk Studio.
# Build:  .venv\Scripts\pyinstaller build\VAbkStudio.spec --noconfirm
#
# Produces a single VAbkStudio.exe (the GUI/orchestrator). It does NOT embed Abogen
# or ffmpeg — those are external (provisioned via uv / auto-downloaded / on disk).
# abogen_driver.py is bundled as DATA because it is executed by the EXTERNAL Abogen
# Python, not imported by the frozen app (see app/abogen_client.driver_path).
import os

# SPECPATH is the directory containing this spec file (…/build); project root is its parent.
project_root = os.path.dirname(os.path.abspath(SPECPATH))

a = Analysis(
    [os.path.join(project_root, "run.py")],
    pathex=[project_root],
    binaries=[],
    datas=[(os.path.join(project_root, "app", "abogen_driver.py"), "app")],
    hiddenimports=["requests"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "torch", "abogen"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VAbkStudio",
    icon=os.path.join(project_root, "build", "VAbkStudio.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
