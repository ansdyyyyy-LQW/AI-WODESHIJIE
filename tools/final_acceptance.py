from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_command(name: str, command: list[str], env: dict[str, str] | None = None) -> dict:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=1800,
    )
    return {"name": name, "command": command, "returncode": completed.returncode, "output": completed.stdout[-200_000:]}


def inspect_bridge(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {"status": "NOT_PROVIDED"}
    errors = []
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "META-INF/mods.toml" not in names:
                errors.append("missing META-INF/mods.toml")
            if not any(name.startswith("com/maidaibridge/") and name.endswith(".class") for name in names):
                errors.append("missing Bridge class files")
            mods = zf.read("META-INF/mods.toml").decode("utf-8", errors="replace") if "META-INF/mods.toml" in names else ""
            for token in ("maid_ai_bridge", "touhou_little_maid", "[1.5.3,1.6.0)"):
                if token not in mods:
                    errors.append(f"mods.toml missing {token}")
    except zipfile.BadZipFile:
        errors.append("invalid JAR")
    return {
        "status": "PASS" if not errors else "FAIL",
        "path": str(path),
        "sha256": sha256(path),
        "size": path.stat().st_size,
        "errors": errors,
    }


def inspect_windows_package(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {"status": "NOT_PROVIDED"}
    if path.is_dir():
        exe = path / "Maid AI Control.exe"
        bridge = list(path.glob("MaidAI-Bridge-*.jar")) + list(path.glob("MaidAI-Bridge.jar"))
        dsh_root = path / "_internal" / "dsh"
        errors = []
        if not exe.is_file():
            errors.append("missing Maid AI Control.exe")
        if not bridge:
            errors.append("missing Bridge JAR")
        if not (path / "README_CN.txt").is_file():
            errors.append("missing README_CN.txt")
        for required in (
            dsh_root / "node" / "node.exe",
            dsh_root / "maidai-profile" / "lib" / "launcher.js",
            dsh_root / "maidai-profile" / "references" / "DEEPSEEK_HARNESS_LOCK.json",
            dsh_root / "maidai-profile" / "node_modules" / "@deepseek-ai" / "dsh" / "package.json",
        ):
            if not required.is_file():
                errors.append(f"missing bundled DSH file: {required.relative_to(path)}")
        return {"status": "PASS" if not errors else "FAIL", "path": str(path), "errors": errors}
    errors = []
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if not any(name.endswith("/Maid AI Control.exe") for name in names):errors.append("missing Maid AI Control.exe")
            if not any(name.endswith(("/MaidAI-Bridge.jar",)) or ("/MaidAI-Bridge-" in name and name.endswith(".jar")) for name in names):errors.append("missing Bridge JAR")
            if not any(name.endswith("/README_CN.txt") for name in names):errors.append("missing README_CN.txt")
            for suffix in (
                "/_internal/dsh/node/node.exe",
                "/_internal/dsh/maidai-profile/lib/launcher.js",
                "/_internal/dsh/maidai-profile/references/DEEPSEEK_HARNESS_LOCK.json",
            ):
                if not any(name.endswith(suffix) for name in names):errors.append(f"missing bundled DSH file: {suffix}")
    except zipfile.BadZipFile:errors.append("invalid Windows package ZIP")
    return {"status":"PASS" if not errors else "FAIL","path":str(path),"sha256":sha256(path),"size":path.stat().st_size,"errors":errors}


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--source-check", action="store_true")
    mode.add_argument("--release-check", action="store_true")
    parser.add_argument("--bridge-jar", type=Path)
    parser.add_argument("--windows-package", type=Path)
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()
    if args.release_check and args.skip_tests:
        parser.error("--release-check 不允许 --skip-tests；正式产物必须运行 Python tests")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([
        str(ROOT / "agent-core" / "src"),
        str(ROOT / "control-center" / "src"),
        str(ROOT / "rnd-runner" / "src"),
        env.get("PYTHONPATH", ""),
    ])
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    commands = []
    if not args.skip_tests:
        test_temp = ROOT / ".test-temp" / "release"
        test_temp.parent.mkdir(parents=True, exist_ok=True)
        commands.append(run_command(
            "python_tests",
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "agent-core/tests",
                "control-center/tests",
                "rnd-runner/tests",
                "--basetemp",
                str(test_temp),
            ],
            env,
        ))
    commands.append(run_command("source_validation",[sys.executable, "tools/validate_source.py"], env))
    bridge_path = args.bridge_jar
    if bridge_path is None:
        candidates = sorted((ROOT / "maid-ai-bridge" / "build" / "libs").glob("MaidAI-Bridge-*.jar"))
        bridge_path = candidates[-1] if candidates else None
    package_path = args.windows_package
    if package_path is None and (ROOT / "dist" / "MaidAI").exists():
        package_path = ROOT / "dist" / "MaidAI"
    report = {
        "mode":"release-check" if args.release_check else "source-check",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "automated_scope": "source, Python, protocol, Forge JAR and Windows package when provided",
        "commands": commands,
        "bridge": inspect_bridge(bridge_path),
        "windows_package": inspect_windows_package(package_path),
        "real_game_acceptance": {
            "status": "NOT_RUN",
            "reason": "A real Minecraft 1.20.1 Forge client/world is required; automated CI must not claim gameplay success.",
            "checklist": "docs/REAL_GAME_ACCEPTANCE_CHECKLIST.md",
        },
    }
    command_ok = all(row["returncode"] == 0 for row in commands)
    report["source_status"]="PASS" if command_ok else "FAIL"
    if args.release_check:
        artifacts_ok=report["bridge"]["status"]=="PASS" and report["windows_package"]["status"]=="PASS"
        report["release_status"]="PASS" if command_ok and artifacts_ok else "INCOMPLETE" if "NOT_PROVIDED" in {report["bridge"]["status"],report["windows_package"]["status"]} else "FAIL"
    else:
        report["release_status"]="NOT_EVALUATED"
    output_dir = ROOT / "validation"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "AUTOMATED_ACCEPTANCE.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Automated Acceptance Report",
        "",
        f"- Mode: **{report['mode']}**",
        f"- Source status: **{report['source_status']}**",
        f"- Release status: **{report['release_status']}**",
        f"- Bridge: **{report['bridge']['status']}**",
        f"- Windows package: **{report['windows_package']['status']}**",
        "- Real Minecraft gameplay: **NOT_RUN**",
        "",
        "自动测试通过不等于真实游戏内 Day 1、丧尸波次、重启恢复和中型建筑场景已经通过。",
        "真实验收必须按 `docs/REAL_GAME_ACCEPTANCE_CHECKLIST.md` 执行。",
    ]
    (output_dir / "AUTOMATED_ACCEPTANCE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    accepted=report["release_status"]=="PASS" if args.release_check else report["source_status"]=="PASS"
    if not accepted:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
