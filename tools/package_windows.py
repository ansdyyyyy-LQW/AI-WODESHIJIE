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
DSH_LOCK = ROOT / "references" / "DEEPSEEK_HARNESS_LOCK.json"
SOURCE_EXCLUDED = {
    ".git", ".venv", ".runtime", ".gradle", "build", "dist", "run", "validation",
    "__pycache__", ".pytest_cache", ".test-temp", ".test-tmp", ".tools", "node_modules",
}


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


def run_tool(command: list[str], *, cwd: Path = ROOT) -> None:
    subprocess.run(command, check=True, cwd=cwd)


def reparse_points(root: Path) -> list[Path]:
    found: list[Path] = []
    for directory, directories, filenames in os.walk(root, topdown=True):
        base = Path(directory)
        safe_directories: list[str] = []
        for name in directories:
            path = base / name
            is_reparse = path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())
            if is_reparse:
                found.append(path)
            else:
                safe_directories.append(name)
        directories[:] = safe_directories
        for name in filenames:
            path = base / name
            if path.is_symlink():
                found.append(path)
    return found


def bundle_deepseek_harness(destination: Path) -> dict[str, str]:
    lock = json.loads(DSH_LOCK.read_text(encoding="utf-8"))
    node = Path(shutil.which("node.exe") or shutil.which("node") or "")
    pnpm = Path(shutil.which("pnpm.cmd") or shutil.which("pnpm") or "")
    if not node.is_file() or not pnpm.is_file():
        raise SystemExit("Node or pnpm is unavailable; cannot build the bundled DeepSeek Harness")
    node_version = subprocess.check_output([str(node), "--version"], text=True, encoding="utf-8").strip().lstrip("v")
    if node_version != str(lock.get("node_version") or ""):
        raise SystemExit(f"Bundled Node version mismatch: expected {lock.get('node_version')}, got {node_version}")

    dsh_root = destination / "_internal" / "dsh"
    profile = dsh_root / "maidai-profile"
    node_dir = dsh_root / "node"
    if dsh_root.exists():
        shutil.rmtree(dsh_root)
    node_dir.mkdir(parents=True)
    shutil.copy2(node, node_dir / "node.exe")
    source = ROOT / "dsh-integration"
    profile.mkdir(parents=True)
    for directory in ("lib", "profile"):
        shutil.copytree(source / directory, profile / directory)
    for filename in (
        "cordis.patch.yml", "package.json", "README.md", "pnpm-lock.yaml", "pnpm-workspace.yaml",
    ):
        shutil.copy2(source / filename, profile / filename)
    # A normal pnpm Windows install uses absolute junction targets. They work
    # only on the build machine, so the distributable uses real hoisted files.
    (profile / ".npmrc").write_text("node-linker=hoisted\n", encoding="utf-8")
    run_tool([
        str(pnpm), "--dir", str(profile), "--config.node-linker=hoisted",
        "install", "--prod", "--frozen-lockfile",
    ])
    links = reparse_points(profile / "node_modules")
    if links:
        raise SystemExit(
            "Bundled DeepSeek Harness contains non-portable links: "
            + ", ".join(str(path) for path in links[:10])
        )
    references = profile / "references"
    references.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DSH_LOCK, references / DSH_LOCK.name)
    required = [
        profile / "lib" / "launcher.js",
        profile / "node_modules" / "@deepseek-ai" / "dsh" / "package.json",
        profile / "node_modules" / "@deepseek-ai" / "dsh-agent" / "package.json",
        references / DSH_LOCK.name,
        node_dir / "node.exe",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("Bundled DeepSeek Harness is incomplete: " + ", ".join(missing))
    package = json.loads((profile / "node_modules" / "@deepseek-ai" / "dsh" / "package.json").read_text(encoding="utf-8"))
    if str(package.get("version") or "") != str(lock.get("version") or ""):
        raise SystemExit("Deployed DSH package does not match the locked version")
    return {"node": node_version, "dsh": str(lock["version"]), "profile": str(lock["profile_version"])}


def copy_source_workspace(destination: Path) -> None:
    dsh_root = (ROOT / "dsh-integration").resolve()
    def ignored(directory: str, names: list[str]) -> set[str]:
        result = {name for name in names if name in SOURCE_EXCLUDED}
        if Path(directory).resolve() == dsh_root:
            result.add("lib")
        return result
    shutil.copytree(
        ROOT, destination,
        ignore=ignored,
    )


def source_files() -> list[Path]:
    files: list[Path] = []
    for directory, directories, filenames in os.walk(ROOT, topdown=True):
        base = Path(directory)
        relative_base = base.relative_to(ROOT)
        directories[:] = [
            name for name in directories
            if name not in SOURCE_EXCLUDED
            and not (relative_base == Path("dsh-integration") and name == "lib")
        ]
        files.extend(base / name for name in filenames)
    return files


def write_source_zip() -> Path:
    source_zip = DIST_ROOT / f"MaidAI-Complete-Source-{VERSION}.zip"
    DIST_ROOT.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in source_files():
            archive.write(path, Path(f"MaidAI-Source-{VERSION}") / path.relative_to(ROOT))
    return source_zip


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
    dsh_versions = bundle_deepseek_harness(DIST)
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
        "deepseek_harness": dsh_versions,
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
    source_zip = write_source_zip()
    print(zip_path)
    print(source_zip)


if __name__ == "__main__":
    if sys.argv[1:] == ["--source-only"]:
        print(write_source_zip())
    else:
        main()
