from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "references" / "REFERENCE_LOCK.json"


def run(args: list[str], cwd: Path | None = None) -> None:
    subprocess.run(args, cwd=cwd, check=True, timeout=900)


def safe_name(name: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in name).strip("-")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    data = json.loads(LOCK.read_text(encoding="utf-8"))
    root = ROOT / "references" / "clones"
    if args.clean and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    for row in data["references"]:
        target = root / safe_name(row["name"])
        if args.verify_only:
            output = subprocess.check_output(["git", "ls-remote", row["url"], row["commit"]], text=True, timeout=120)
            if row["commit"] not in output:
                # Some servers do not advertise arbitrary SHA; fetch the branch ref instead.
                output = subprocess.check_output(["git", "ls-remote", row["url"], row["branch"]], text=True, timeout=120)
                if row["commit"] not in output:
                    raise SystemExit(f"reference lock mismatch: {row['name']}")
            print(f"VERIFY {row['name']} {row['commit']}")
            continue
        if not (target / ".git").exists():
            run(["git", "clone", "--no-checkout", "--filter=blob:none", row["url"], str(target)])
        run(["git", "fetch", "--depth", "1", "origin", row["commit"]], cwd=target)
        run(["git", "checkout", "--detach", row["commit"]], cwd=target)
        print(f"LOCKED {row['name']} {row['commit']}")


if __name__ == "__main__":
    main()
