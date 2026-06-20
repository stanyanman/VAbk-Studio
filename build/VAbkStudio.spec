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

# Embed a Windows version resource (shown in the exe's file properties) derived
# from app/__init__.py, so __version__ stays the single source of truth.
import re
with open(os.path.join(project_root, "app", "__init__.py"), encoding="utf-8") as _f:
    _ver = re.search(r'__version__\s*=\s*"([^"]+)"', _f.read()).group(1)
_vt = (tuple(int(p) for p in _ver.split(".")) + (0, 0, 0, 0))[:4]
_version_file = os.path.join(project_root, "build", "_version_info.txt")
with open(_version_file, "w", encoding="utf-8") as _f:
    _f.write(
        "VSVersionInfo(\n"
        "  ffi=FixedFileInfo(\n"
        f"    filevers={_vt}, prodvers={_vt}, mask=0x3f, flags=0x0,\n"
        "    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)\n"
        "  ),\n"
        "  kids=[\n"
        "    StringFileInfo([StringTable('040904B0', [\n"
        "      StringStruct('CompanyName', 'stanyanman'),\n"
        "      StringStruct('FileDescription', 'VAbk Studio'),\n"
        f"      StringStruct('FileVersion', '{_ver}'),\n"
        "      StringStruct('InternalName', 'VAbkStudio'),\n"
        "      StringStruct('OriginalFilename', 'VAbkStudio.exe'),\n"
        "      StringStruct('ProductName', 'VAbk Studio'),\n"
        f"      StringStruct('ProductVersion', '{_ver}'),\n"
        "    ])]),\n"
        "    VarFileInfo([VarStruct('Translation', [1033, 1200])])\n"
        "  ]\n"
        ")\n"
    )

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
    version=_version_file,
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
