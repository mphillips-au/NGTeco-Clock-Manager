# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for NGTeco Clock Manager.

Supports two build modes:
  - release (default): Windowed GUI subsystem (no console window), embedded
    Windows PE version resources, packaged for production installation.
  - dev: Console enabled (stdout/stderr immediately visible in command prompt)
    for debugging, diagnostics, and developer inspection.

Select mode with the CLOCKMANAGER_BUILD_MODE environment variable:
  set CLOCKMANAGER_BUILD_MODE=dev      # or release
"""

from __future__ import annotations

import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

SPEC_ROOT = Path(SPECPATH).resolve()
PROJECT_ROOT = SPEC_ROOT.parent
SRC_ROOT = PROJECT_ROOT / "src"

BUILD_MODE = os.environ.get("CLOCKMANAGER_BUILD_MODE", "release").lower().strip()
IS_DEV = BUILD_MODE == "dev"

APP_NAME = "clockmanager-dev" if IS_DEV else "clockmanager"
ICON_PATH = PROJECT_ROOT / "packaging" / "assets" / "clockmanager.ico"
VERSION_FILE = PROJECT_ROOT / "packaging" / "version_info.txt"

# Collect data files
datas = []
datas += collect_data_files("tzdata")
datas += collect_data_files("clockmanager")
if ICON_PATH.is_file():
    datas.append((str(ICON_PATH), "assets"))

# Collect submodules
hiddenimports = [
    "greenlet",
    "sqlite3",
    "tzdata",
    "zk",
    "clockmanager.windows",
]
hiddenimports += collect_submodules("clockmanager")
hiddenimports += collect_submodules("sqlalchemy.dialects.sqlite")

excludes = [
    "tkinter",
    "unittest",
    "IPython",
    "matplotlib",
    "scipy",
    "numpy",
    "pandas",
]

a = Analysis(
    [str(SRC_ROOT / "clockmanager" / "__main__.py")],
    pathex=[str(SRC_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher,
)

exe_kwargs = {
    "name": APP_NAME,
    "debug": IS_DEV,
    "bootloader_ignore_signals": False,
    "strip": False,
    "upx": False,
    "console": IS_DEV,  # Dev has console window; Release runs purely as GUI
    "disable_windowed_traceback": False,
    "argv_emulation": False,
    "target_arch": None,
    "codesign_identity": None,
    "entitlements_file": None,
}

if ICON_PATH.is_file():
    exe_kwargs["icon"] = str(ICON_PATH)

if not IS_DEV and VERSION_FILE.is_file():
    exe_kwargs["version"] = str(VERSION_FILE)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    **exe_kwargs,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
