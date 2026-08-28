from __future__ import annotations

import os
import tomllib
import zipfile
from pathlib import Path
from typing import Any


TLM_IDS = {"touhou_little_maid", "touhoulittlemaid"}
BRIDGE_ID = "maid_ai_bridge"


def _mods_from_jar(path: Path) -> list[dict[str, str]]:
    try:
        with zipfile.ZipFile(path) as archive:
            raw = archive.read("META-INF/mods.toml")
        parsed = tomllib.loads(raw.decode("utf-8", errors="replace"))
    except (OSError, KeyError, zipfile.BadZipFile, tomllib.TOMLDecodeError):
        return []
    rows = parsed.get("mods") or []
    if not isinstance(rows, list):
        return []
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        mod_id = str(row.get("modId") or "").strip().lower()
        if mod_id:
            result.append(
                {
                    "mod_id": mod_id,
                    "version": str(row.get("version") or "unknown"),
                    "jar": str(path),
                }
            )
    return result


def inspect_minecraft_dir(path: str | Path) -> dict[str, Any]:
    if not str(path).strip():
        return {"path": "", "path_valid": False, "tlm_found": False, "bridge_found": False, "tlm": None, "bridge": None, "mods": [], "ready": False}
    root = Path(path).expanduser()
    valid = root.is_dir() and (
        (root / "mods").is_dir()
        or (root / "versions").is_dir()
        or (root / "launcher_profiles.json").is_file()
    )
    mods: list[dict[str, str]] = []
    mods_dir = root / "mods"
    if mods_dir.is_dir():
        for jar in sorted(mods_dir.glob("*.jar")):
            mods.extend(_mods_from_jar(jar))
    tlm = next((row for row in mods if row["mod_id"] in TLM_IDS), None)
    bridge = next((row for row in mods if row["mod_id"] == BRIDGE_ID), None)
    return {
        "path": str(root.resolve()) if root.exists() else str(root),
        "path_valid": valid,
        "tlm_found": tlm is not None,
        "bridge_found": bridge is not None,
        "tlm": tlm,
        "bridge": bridge,
        "mods": mods,
        "ready": bool(valid and tlm and bridge),
    }


def candidate_minecraft_dirs(configured: str = "") -> list[Path]:
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    candidates.append(appdata / ".minecraft")
    candidates.extend((appdata / "PrismLauncher" / "instances").glob("*/minecraft"))
    candidates.extend((Path.home() / "curseforge" / "minecraft" / "Instances").glob("*"))
    candidates.extend((Path.home() / "Documents" / "Curse" / "Minecraft" / "Instances").glob("*"))
    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def locate_minecraft(configured: str = "") -> dict[str, Any]:
    inspected = [inspect_minecraft_dir(path) for path in candidate_minecraft_dirs(configured)]
    return next(
        (row for row in inspected if row["ready"]),
        next((row for row in inspected if row["path_valid"]), inspect_minecraft_dir(configured)),
    )
