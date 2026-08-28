# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path.cwd().resolve()
datas, binaries, hiddenimports = [], [], []
for package in ("websockets", "httpx", "pydantic", "keyring"):
    d, b, h = collect_all(package)
    datas += d; binaries += b; hiddenimports += h
hiddenimports += collect_submodules("maid_agent")
datas += [(str(ROOT / "agent-core" / "src" / "maid_agent" / "prompts"), "maid_agent/prompts")]

a = Analysis(
    [str(ROOT / "agent-core" / "src" / "maid_agent" / "main.py")],
    pathex=[str(ROOT / "agent-core" / "src")],
    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
    excludes=["PySide6", "pytest"], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="maid-agent", debug=False, strip=False, upx=False, console=True,
)
