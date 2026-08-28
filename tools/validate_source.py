from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REQUIRED=[
    "agent-core/src/maid_agent/main.py",
    "agent-core/src/maid_agent/brain/autonomous_loop.py",
    "agent-core/src/maid_agent/control/api.py",
    "agent-core/src/maid_agent/rnd/harness.py",
    "control-center/src/maid_ai_control/main.py",
    "control-center/src/maid_ai_control/process_supervisor.py",
    "rnd-runner/src/maid_rnd_runner/harness.py",
    "maid-ai-bridge/src/main/java/com/maidaibridge/controller/MaidAiController.java",
    "maid-ai-bridge/src/main/java/com/maidaibridge/action/ActionEngine.java",
]


def main()->int:
    errors=[]
    for relative in REQUIRED:
        path=ROOT/relative
        if not path.is_file():errors.append(f"missing:{relative}")
    for root in (ROOT/"agent-core"/"src",ROOT/"control-center"/"src",ROOT/"rnd-runner"/"src"):
        for path in root.rglob("*.py"):
            try:ast.parse(path.read_text(encoding="utf-8"),filename=str(path))
            except Exception as exc:errors.append(f"python_syntax:{path.relative_to(ROOT)}:{exc}")
    forbidden=("level.setBlock(","setBlockAndUpdate(","teleportTo(","/give","/tp ")
    for path in (ROOT/"maid-ai-bridge"/"src"/"main"/"java").rglob("*.java"):
        text=path.read_text(encoding="utf-8")
        for value in forbidden:
            if value in text:errors.append(f"forbidden_world_shortcut:{path.relative_to(ROOT)}:{value}")
    if errors:
        print("\n".join(errors));return 1
    print("SOURCE_VALIDATION_OK");return 0

if __name__=="__main__":raise SystemExit(main())
