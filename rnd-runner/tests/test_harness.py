from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from maid_rnd_runner.harness import CommandResult, HarnessRunner, ValidationFailure


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


def _write_brief(
    root: Path, *, target: str = "MAIDAI_SOURCE", project_id: str = "project-a",
    allowed: list[str] | None = None,
) -> None:
    control = root / ".maidai-rnd"
    control.mkdir(parents=True, exist_ok=True)
    if allowed is None:
        allowed = ["agent-core/"] if target == "MAIDAI_SOURCE" else ["rnd-projects/<project-id>/"]
    (control / "brief.json").write_text(json.dumps({
        "project_id": project_id,
        "development_target": target,
        "allowed_areas": allowed,
    }), encoding="utf-8")


def _addon_project(root: Path, project_id: str = "project-a") -> Path:
    project = root / "rnd-projects" / project_id
    (project / "gradle" / "wrapper").mkdir(parents=True)
    (project / "build.gradle").write_text("plugins { id 'java' }\n", encoding="utf-8")
    (project / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    (project / "gradlew.bat").write_text("@echo off\r\n", encoding="utf-8")
    return project


def _forge_jar(path: Path, *, valid: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("demo/Example.class", b"class-bytes")
        if valid:
            archive.writestr("META-INF/mods.toml", """
modLoader="javafml"
loaderVersion="[47,)"
license="MIT"
[[mods]]
modId="examplemod"
version="1.0.0"
displayName="Example Mod"
[[dependencies.examplemod]]
modId="forge"
mandatory=true
versionRange="[47.4,)"
ordering="NONE"
side="BOTH"
[[dependencies.examplemod]]
modId="minecraft"
mandatory=true
versionRange="[1.20.1,1.21)"
ordering="NONE"
side="BOTH"
""")


def test_harness_reads_source_and_exports_modified_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    input_dir = tmp_path / "input"
    output = tmp_path / "output"
    input_dir.mkdir()
    _minimal_source(source)
    baseline = _baseline(source)
    _write_brief(source)
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
    assert "agent-core/src/demo/__init__.py" in result["changed_files"]
    assert result["modified_files"] == ["agent-core/src/demo/__init__.py"]
    assert result["created_files"] == [".maidai-rnd/brief.json"] and result["deleted_files"] == []
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
    _write_brief(source)
    (source / "agent-core" / "src" / "demo" / "broken.py").write_text(
        "def broken(:\n    pass\n", encoding="utf-8",
    )
    control = source / ".maidai-rnd"
    control.mkdir(exist_ok=True)
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


def test_allowed_area_rules_expand_project_and_keep_targets_isolated(tmp_path: Path) -> None:
    runner = HarnessRunner(tmp_path, tmp_path, tmp_path / "output", "cycle")
    runner.brief = {
        "project_id": "project-a", "development_target": "NEW_FORGE_ADDON",
        "allowed_areas": ["rnd-projects/<project-id>/"],
    }
    runner.changed_files = ["rnd-projects/project-a/src/main/java/Demo.java", ".maidai-rnd/final_result.json"]
    runner._validate_changed_areas()

    runner.changed_files = ["rnd-projects/project-a/src/main/java/Demo.java", "agent-core/x.py"]
    with pytest.raises(ValidationFailure, match="agent-core/x.py") as outside:
        runner._validate_changed_areas()
    assert outside.value.code == "RND_CHANGE_OUTSIDE_ALLOWED_AREAS"

    runner.brief = {
        "project_id": "maidai-source", "development_target": "MAIDAI_SOURCE",
        "allowed_areas": ["dsh-integration/"],
    }
    runner.changed_files = ["dsh-integration/src/driver.ts"]
    runner._validate_changed_areas()


def test_forge_addon_missing_project_fails(tmp_path: Path) -> None:
    runner = HarnessRunner(tmp_path, tmp_path / "source", tmp_path / "output", "cycle")
    runner.source.mkdir()
    runner.brief = {"project_id": "project-a", "development_target": "NEW_FORGE_ADDON"}
    with pytest.raises(ValidationFailure) as missing:
        runner._build_and_validate_forge_addon()
    assert missing.value.code == "FORGE_ADDON_PROJECT_MISSING"


def test_forge_addon_build_failure_is_final_validator_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    _addon_project(source)
    runner = HarnessRunner(tmp_path, source, tmp_path / "output", "cycle")
    runner.brief = {"project_id": "project-a", "development_target": "NEW_FORGE_ADDON"}

    def fail_build(name: str, command: list[str], cwd: Path) -> CommandResult:
        row = CommandResult(name, command, str(cwd), 1, "stdout.log", "stderr.log")
        runner.commands.append(row)
        return row

    monkeypatch.setattr(runner, "_run_command", fail_build)
    with pytest.raises(ValidationFailure) as failed:
        runner._build_and_validate_forge_addon()
    assert failed.value.code == "FORGE_ADDON_BUILD_FAILED"


def test_forge_addon_requires_valid_mod_jar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    project = _addon_project(source)
    _forge_jar(project / "build" / "libs" / "plain.jar", valid=False)
    runner = HarnessRunner(tmp_path, source, tmp_path / "output", "cycle")
    runner.brief = {"project_id": "project-a", "development_target": "NEW_FORGE_ADDON"}

    def pass_build(name: str, command: list[str], cwd: Path) -> CommandResult:
        row = CommandResult(name, command, str(cwd), 0, "stdout.log", "stderr.log")
        runner.commands.append(row)
        return row

    monkeypatch.setattr(runner, "_run_command", pass_build)
    with pytest.raises(ValidationFailure) as invalid:
        runner._build_and_validate_forge_addon()
    assert invalid.value.code == "FORGE_ADDON_ARTIFACT_INVALID"


def test_valid_forge_jar_is_exported_with_complete_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    project = _addon_project(source)
    jar = project / "build" / "libs" / "examplemod-1.0.0.jar"
    _forge_jar(jar)
    runner = HarnessRunner(tmp_path, source, tmp_path / "output", "cycle")
    runner.brief = {"project_id": "project-a", "development_target": "NEW_FORGE_ADDON"}

    def pass_build(name: str, command: list[str], cwd: Path) -> CommandResult:
        row = CommandResult(name, command, str(cwd), 0, "stdout.log", "stderr.log")
        runner.commands.append(row)
        return row

    monkeypatch.setattr(runner, "_run_command", pass_build)
    metadata = runner._build_and_validate_forge_addon()
    result: dict[str, object] = {"artifacts": []}
    runner._collect_artifacts(result, metadata)
    mod = next(item for item in result["artifacts"] if item["type"] == "forge_mod")  # type: ignore[index]
    assert mod["mod_id"] == "examplemod"
    assert mod["version"] == "1.0.0"
    assert mod["minecraft"] == "1.20.1" and mod["loader"] == "forge"
    assert mod["build_status"] == "PASS"
    assert Path(mod["path"]).is_file() and mod["size"] > 0
