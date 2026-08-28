# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path.cwd().resolve()
datas, binaries, hiddenimports = [], [], []
for package in ("websockets", "pydantic", "keyring"):
    hiddenimports += collect_submodules(package)
hiddenimports += collect_submodules("maid_ai_control")

a = Analysis(
    [str(ROOT / "control-center" / "src" / "maid_ai_control" / "main.py")],
    pathex=[str(ROOT / "control-center" / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="Maid AI Control", debug=False, strip=False, upx=False,
    console=False,
    version=str(ROOT / "control-center" / "packaging" / "version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Maid AI Control")
