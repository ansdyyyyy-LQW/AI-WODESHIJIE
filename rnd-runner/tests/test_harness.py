from __future__ import annotations

import json
import subprocess
from pathlib import Path

from maid_rnd_runner.harness import HarnessRunner


def _minimal_source(root: Path) -> None:
    package = root / "agent-core" / "src" / "demo"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")


def _baseline(root: Path) -> str:
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.name", "MaidAI R&D Test"],
        ["git", "config", "user.email", "rnd-test@local.invalid"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "baseline"],
    ):
        subprocess.run(command, cwd=root, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True,
        text=True, stdout=subprocess.PIPE,
    ).stdout.strip()


def test_harness_reads_source_and_exports_modified_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    input_dir = tmp_path / "input"
    output = tmp_path / "output"
    input_dir.mkdir()
    _minimal_source(source)
    baseline = _baseline(source)
    (source / "agent-core" / "src" / "demo" / "__init__.py").write_text(
        "VALUE = 2\n", encoding="utf-8",
    )
    code = HarnessRunner(
        input_dir, source, output, "cycle-001", baseline_commit=baseline,
    ).run()
    assert code == 0
    result = json.loads((output / "runner_result.json").read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["patch_applied"] is False
    assert result["validator_decision"] == "PASS"
    assert result["changed_files"] == ["agent-core/src/demo/__init__.py"]
    assert result["modified_files"] == ["agent-core/src/demo/__init__.py"]
    assert result["created_files"] == [] and result["deleted_files"] == []
    assert result["artifacts"]
    assert Path(result["artifacts"][0]["path"]).is_file()
    assert (output / "artifact_manifest.json").is_file()
    assert (output / "git-diff.patch").is_file()


def test_validator_fails_real_compile_error_even_when_dsh_claims_complete(tmp_path: Path) -> None:
    source = tmp_path / "source"
    input_dir = tmp_path / "input"
    output = tmp_path / "output"
    input_dir.mkdir()
    _minimal_source(source)
    baseline = _baseline(source)
    (source / "agent-core" / "src" / "demo" / "broken.py").write_text(
        "def broken(:\n    pass\n", encoding="utf-8",
    )
    control = source / ".maidai-rnd"
    control.mkdir()
    (control / "final_result.json").write_text(
        json.dumps({"claimed": "COMPLETED"}), encoding="utf-8",
    )

    code = HarnessRunner(
        input_dir, source, output, "cycle-compile-fail", baseline_commit=baseline,
    ).run()

    assert code == 1
    result = json.loads((output / "runner_result.json").read_text(encoding="utf-8"))
    assert result["ok"] is False and result["validator_decision"] == "FAIL"
    compile_result = next(item for item in result["commands"] if item["name"] == "agent_compile")
    assert compile_result["returncode"] != 0
