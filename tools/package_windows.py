from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.3.0"
DIST_ROOT = ROOT / "dist"
DIST = DIST_ROOT / "MaidAI"
BUILD = ROOT / "build" / "pyinstaller"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_spec(name: str) -> None:
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", str(BUILD / "dist"), "--workpath", str(BUILD / "work" / name),
        str(ROOT / "control-center" / "packaging" / f"{name}.spec"),
    ], check=True, cwd=ROOT)


def copy_source_workspace(destination: Path) -> None:
    excluded = {".git", ".venv", ".runtime", ".gradle", "build", "dist", "run", "validation", "__pycache__", ".pytest_cache", ".test-temp", ".tools"}
    shutil.copytree(
        ROOT, destination,
        ignore=lambda _directory, names: {name for name in names if name in excluded},
    )


def main() -> None:
    if os.name != "nt":
        raise SystemExit("Windows product packaging must run on Windows because PyInstaller cannot cross-build a real .exe")
    bridge_candidates = sorted((ROOT / "maid-ai-bridge" / "build" / "libs").glob("MaidAI-Bridge-*.jar"))
    if not bridge_candidates:
        raise SystemExit("Bridge JAR not found; build the Forge Bridge first")
    shutil.rmtree(BUILD, ignore_errors=True)
    shutil.rmtree(DIST, ignore_errors=True)
    BUILD.mkdir(parents=True, exist_ok=True)

    run_spec("maid_ai_control")
    run_spec("maid_agent")
    run_spec("maid_rnd")

    gui = BUILD / "dist" / "Maid AI Control"
    gui_exe = gui / "Maid AI Control.exe"
    agent_exe = BUILD / "dist" / "maid-agent.exe"
    rnd_exe = BUILD / "dist" / "maid-rnd.exe"
    for required in (gui_exe, agent_exe, rnd_exe):
        if not required.is_file():
            raise SystemExit(f"PyInstaller output missing: {required}")

    shutil.copytree(gui, DIST)
    agent_dir = DIST / "resources" / "MaidAgent"
    rnd_dir = DIST / "resources" / "rnd-harness"
    agent_dir.mkdir(parents=True, exist_ok=True)
    rnd_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(agent_exe, agent_dir / agent_exe.name)
    shutil.copy2(rnd_exe, rnd_dir / rnd_exe.name)
    copy_source_workspace(DIST / "resources" / "rnd-source")

    bridge = bridge_candidates[-1]
    shutil.copy2(bridge, DIST / "MaidAI-Bridge.jar")
    shutil.copy2(ROOT / "README_CN.txt", DIST / "README_CN.txt")
    shutil.copy2(ROOT / "docs" / "REAL_GAME_ACCEPTANCE_CHECKLIST.md", DIST / "实机验收清单.md")

    manifest = {
        "product": "Maid AI", "version": VERSION,
        "minecraft": "1.20.1", "forge": "[47.4.0,48)", "forge_detection": "47.x",
        "bridge_declared_forge": "[47.4.0,48)", "bridge_build_forge": "47.4.23", "tlm": "1.5.3",
        "artifacts": [],
    }
    for path in sorted(p for p in DIST.rglob("*") if p.is_file()):
        manifest["artifacts"].append({
            "path": path.relative_to(DIST).as_posix(),
            "size": path.stat().st_size,
            "sha256": sha256(path),
        })
    (DIST / "VERSION_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    checksums = [
        f"{sha256(path)}  {path.relative_to(DIST).as_posix()}"
        for path in sorted(p for p in DIST.rglob("*") if p.is_file() and p.name != "SHA256SUMS.txt")
    ]
    (DIST / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")

    zip_path = DIST_ROOT / f"MaidAI-Windows-{VERSION}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in DIST.rglob("*"):
            if path.is_file(): archive.write(path, Path("MaidAI") / path.relative_to(DIST))
    source_zip = DIST_ROOT / f"MaidAI-Complete-Source-{VERSION}.zip"
    source_excluded = {".git", ".venv", ".runtime", ".gradle", "build", "dist", "run", "validation", "__pycache__", ".pytest_cache", ".test-temp", ".tools"}
    with zipfile.ZipFile(source_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in ROOT.rglob("*"):
            if path.is_file() and not any(part in source_excluded for part in path.relative_to(ROOT).parts):
                archive.write(path, Path(f"MaidAI-Source-{VERSION}") / path.relative_to(ROOT))
    print(zip_path)
    print(source_zip)


if __name__ == "__main__":
    main()
