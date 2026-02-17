# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Cooldown Reader.

Builds a --onedir distribution so the config/ folder sits next to the exe
and can be read/written at runtime.

EasyOCR + torch are excluded because the OCR module is stubbed out and
including them would add ~700 MB to the build.
"""

import os

block_cipher = None

a = Analysis(
    ["build_entry.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("config/default_config.json", "config"),
    ],
    hiddenimports=[
        "keyboard",
        "keyboard._winkeyboard",
        "mss",
        "mss.windows",
        "cv2",
        "numpy",
        "PyQt6",
        "PyQt6.sip",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "easyocr",
        "torch",
        "torchvision",
        "torchaudio",
        "scipy",
        "pandas",
        "matplotlib",
        "tkinter",
        "unittest",
        "test",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CooldownReader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="CooldownReader",
)
