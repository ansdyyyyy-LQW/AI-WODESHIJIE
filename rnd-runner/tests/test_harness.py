from __future__ import annotations

import json
from pathlib import Path

from maid_rnd_runner.harness import HarnessRunner


def _minimal_source(root: Path) -> None:
    package = root / "agent-core" / "src" / "demo"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")


def test_harness_reads_source_and_exports_modified_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    input_dir = tmp_path / "input"
    output = tmp_path / "output"
    input_dir.mkdir()
    _minimal_source(source)
    (input_dir / "change_request.json").write_text(json.dumps({"unified_diff": "", "verification_commands": []}), encoding="utf-8")
    code = HarnessRunner(input_dir, source, output, "cycle-001").run()
    assert code == 0
    result = json.loads((output / "runner_result.json").read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["artifacts"]
    assert Path(result["artifacts"][0]["path"]).is_file()
