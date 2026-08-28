# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path.cwd().resolve()
datas, binaries, hiddenimports = [], [], []
for package in ("pytest", "pytest_asyncio", "pydantic", "websockets", "httpx", "keyring"):
    d, b, h = collect_all(package)
    datas += d; binaries += b; hiddenimports += h
hiddenimports += collect_submodules("maid_rnd_runner")
a = Analysis(
    [str(ROOT / "rnd-runner" / "src" / "maid_rnd_runner" / "main.py")],
    pathex=[str(ROOT / "rnd-runner" / "src")],
    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
    excludes=["PySide6", "tkinter"], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="maid-rnd", debug=False, strip=False, upx=False, console=True,
)
