# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Section 8 GameSpy backend. Build with: pyinstaller section8-server.spec

The news file is not bundled. It has to stay editable next to the executable, so the release archive
ships news/section8_news.txt alongside and server/news.py resolves it from there.
"""

from pathlib import Path

ROOT = Path(SPECPATH)

a = Analysis(
    ["run.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    # The server is standard library only, so nothing is loaded dynamically.
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "PIL",
        "numpy",
        "pandas",
        "scipy",
        "IPython",
        "jupyter",
        "notebook",
        "pytest",
        "coverage",
        "mypy",
        "ruff",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="section8-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
