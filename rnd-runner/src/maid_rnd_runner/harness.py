from __future__ import annotations

import compileall
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

IGNORE = {".git", ".gradle", ".venv", ".runtime", "build", "dist", "__pycache__", ".pytest_cache", "logs", "run", "data", "node_modules"}


@dataclass
class CommandResult:
    name: str
    command: list[str]
    cwd: str
    returncode: int
    stdout_log: str
    stderr_log: str


class HarnessRunner:
    """Production R&D runner for the isolated source copy prepared by Agent Core."""

    def __init__(
        self, input_dir: Path, source: Path, output: Path, cycle_id: str,
        *, baseline_commit: str = "",
    ):
        self.input_dir = input_dir.resolve()
        self.source = source.resolve()
        self.output = output.resolve()
        self.cycle_id = cycle_id
        self.logs = self.output / "verification"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.commands: list[CommandResult] = []
        self.baseline_commit = baseline_commit.strip()
        self.changed_files: list[str] = []

    def run(self) -> int:
        result: dict[str, Any] = {
            "cycle_id": self.cycle_id,
            "source": str(self.source),
            "validation_mode": "direct_workspace",
            "patch_applied": False,
            "commands": [],
            "artifacts": [],
            "ok": False,
        }
        try:
            if not self.source.is_dir():
                raise RuntimeError("isolated source workspace is missing")
            diff = self._workspace_diff()
            result.update(diff)
            manifest = self._manifest()
            (self.output / "runner_source_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            if not diff["workspace_changed"]:
                raise RuntimeError("DSH workspace contains no change after the Git baseline")
            if self._implementation_change_required() and not any(
                not path.startswith(".maidai-rnd/") for path in self.changed_files
            ):
                raise RuntimeError("DSH did not create an implementation change outside .maidai-rnd")
            self._run_standard_verification()
            for command in self._load_extra_commands():
                self._run_command("requested", command, self.source)
            failed = [row for row in self.commands if row.returncode != 0]
            self._collect_artifacts(result)
            (self.output / "artifact_manifest.json").write_text(
                json.dumps({"cycle_id": self.cycle_id, "artifacts": result["artifacts"]}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            result["commands"] = [asdict(row) for row in self.commands]
            result["ok"] = not failed
            result["failure_count"] = len(failed)
            result["validator_decision"] = "PASS" if not failed else "FAIL"
        except Exception as exc:
            result["error"] = f"{exc.__class__.__name__}: {exc}"
            result["commands"] = [asdict(row) for row in self.commands]
            result["validator_decision"] = "FAIL"
        (self.output / "runner_result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return 0 if result.get("ok") else 1

    def _workspace_diff(self) -> dict[str, Any]:
        if not (self.source / ".git").is_dir():
            raise RuntimeError("isolated DSH workspace is missing its Git baseline")
        baseline = self.baseline_commit
        if not baseline:
            baseline = self._git("rev-list", "--max-parents=0", "HEAD").splitlines()[0].strip()
        self._git("cat-file", "-e", f"{baseline}^{{commit}}")
        # Intent-to-add makes new source files visible in the saved binary diff
        # without committing or altering their contents.
        self._git("add", "-N", "--", ".")
        status = self._git("status", "--porcelain=v1", "--untracked-files=all")
        changed: list[str] = []
        for line in status.splitlines():
            value = line[3:].strip() if len(line) >= 4 else ""
            if " -> " in value:
                value = value.split(" -> ", 1)[1]
            value = value.strip('"').replace("\\", "/")
            if value:
                changed.append(value)
        self.changed_files = sorted(set(changed))
        diff_text = self._git("diff", "--binary", baseline, "--", ".")
        name_status = self._git("diff", "--name-status", baseline, "--", ".")
        created: list[str] = []
        modified: list[str] = []
        deleted: list[str] = []
        for line in name_status.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status_code = parts[0][:1]
            path_value = parts[-1].replace("\\", "/")
            if status_code == "A":
                created.append(path_value)
            elif status_code == "D":
                deleted.append(path_value)
            else:
                modified.append(path_value)
        diff_path = self.output / "workspace.diff"
        delivery_diff = self.output / "git-diff.patch"
        status_path = self.output / "workspace_status.txt"
        diff_path.write_text(diff_text, encoding="utf-8")
        delivery_diff.write_text(diff_text, encoding="utf-8")
        status_path.write_text(status + ("\n" if status else ""), encoding="utf-8")
        return {
            "baseline_commit": baseline,
            "workspace_changed": bool(self.changed_files),
            "changed_files": self.changed_files,
            "modified_files": sorted(set(modified)),
            "created_files": sorted(set(created)),
            "deleted_files": sorted(set(deleted)),
            "diff_path": str(diff_path),
            "git_diff_path": str(delivery_diff),
            "diff_sha256": hashlib.sha256(diff_path.read_bytes()).hexdigest(),
            "status_path": str(status_path),
        }

    def _implementation_change_required(self) -> bool:
        brief_path = self.source / ".maidai-rnd" / "brief.json"
        if not brief_path.is_file():
            return True
        try:
            brief = json.loads(brief_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return True
        return str(brief.get("development_target") or "MAIDAI_SOURCE") not in {
            "RESEARCH_ONLY", "EXTERNAL_MOD_RESEARCH",
        }

    def _git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args], cwd=self.source, env=self._env(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
            text=True, encoding="utf-8", errors="replace",
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} failed: " + (completed.stderr or completed.stdout)[:4000]
            )
        return completed.stdout

    def _manifest(self) -> dict[str, Any]:
        files: list[dict[str, Any]] = []
        root_hash = hashlib.sha256()
        for path in sorted(self.source.rglob("*")):
            if not path.is_file() or any(part in IGNORE for part in path.parts):
                continue
            relative = path.relative_to(self.source).as_posix()
            data = path.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            files.append({"path": relative, "size": len(data), "sha256": sha})
            root_hash.update(relative.encode())
            root_hash.update(bytes.fromhex(sha))
        return {"source_hash": root_hash.hexdigest(), "file_count": len(files), "files": files}

    def _apply_patch(self, patch_path: Path) -> None:
        if not shutil.which("git"):
            raise RuntimeError("git is required to apply an R&D patch")
        if not (self.source / ".git").exists():
            self._check(["git", "init", "-q"], self.source)
            self._check(["git", "config", "user.email", "rnd@local.invalid"], self.source)
            self._check(["git", "config", "user.name", "MaidAI R&D"], self.source)
            self._check(["git", "add", "-A"], self.source)
            self._check(["git", "commit", "-q", "-m", "isolated baseline"], self.source)
        check = subprocess.run(
            ["git", "apply", "--check", str(patch_path)], cwd=self.source,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self._env(), timeout=120,
        )
        if check.returncode != 0:
            raise RuntimeError("proposed patch failed git apply --check: " + check.stderr.decode(errors="replace")[:4000])
        self._check(["git", "apply", str(patch_path)], self.source)

    def _run_standard_verification(self) -> None:
        for name, source_dir in (
            ("agent_compile", self.source / "agent-core" / "src"),
            ("rnd_compile", self.source / "rnd-runner" / "src"),
            ("control_compile", self.source / "control-center" / "src"),
        ):
            if source_dir.exists():
                stdout = io.StringIO()
                stderr = io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    ok = compileall.compile_dir(str(source_dir), quiet=1, force=True)
                detail = f"compileall {source_dir}\n" + stdout.getvalue() + stderr.getvalue()
                self._record_internal(name, 0 if ok else 1, detail)

        test_suites = []
        if any(path.startswith("agent-core/") for path in self.changed_files):
            test_suites.append(("agent_tests", self.source / "agent-core" / "tests", self.source / "agent-core" / "src"))
        if any(path.startswith("rnd-runner/") for path in self.changed_files):
            test_suites.append(("runner_tests", self.source / "rnd-runner" / "tests", self.source / "rnd-runner" / "src"))
        if any(path.startswith("control-center/") for path in self.changed_files):
            test_suites.append(("control_tests", self.source / "control-center" / "tests", self.source / "control-center" / "src"))
        for suite_name, tests, source_dir in test_suites:
            if not tests.exists():
                continue
            try:
                import pytest
                source_text = str(source_dir)
                stdout = io.StringIO()
                stderr = io.StringIO()
                sys.path.insert(0, source_text)
                try:
                    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                        returncode = int(pytest.main(["-q", str(tests), "--disable-warnings"]))
                finally:
                    if sys.path and sys.path[0] == source_text:
                        sys.path.pop(0)
                self._record_internal(
                    suite_name, returncode,
                    "pytest executed in bundled R&D runtime\n" + stdout.getvalue() + stderr.getvalue(),
                )
            except Exception as exc:
                self._record_internal(suite_name, 1, f"pytest failed to start: {exc}\n")

        bridge = self.source / "maid-ai-bridge"
        if bridge.exists() and any(path.startswith("maid-ai-bridge/") for path in self.changed_files):
            wrapper = bridge / ("gradlew.bat" if os.name == "nt" else "gradlew")
            if wrapper.exists():
                if os.name != "nt":
                    wrapper.chmod(wrapper.stat().st_mode | 0o111)
                self._run_command("bridge_build", [str(wrapper), "build", "--no-daemon"], bridge)
            else:
                self._record_internal("bridge_build", 1, "Gradle wrapper is missing; Forge candidate was not built.\n")

    def _load_extra_commands(self) -> list[list[str]]:
        path = self.input_dir / "change_request.json"
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        result: list[list[str]] = []
        for item in raw.get("verification_commands", [])[:8]:
            if isinstance(item, list):
                parts = [str(value) for value in item]
            elif isinstance(item, str):
                import shlex
                parts = shlex.split(item, posix=os.name != "nt")
            else:
                continue
            if not parts:
                continue
            executable = Path(parts[0]).name.lower()
            # Python commands are already run in-process, because a frozen R&D
            # executable is not a general-purpose python.exe.
            allowed = {"gradlew", "gradlew.bat", "git"}
            if executable not in allowed:
                continue
            if any(token in {";", "&&", "||", "|", ">", "<"} for token in parts):
                continue
            result.append(parts)
        return result

    def _run_command(self, name: str, command: list[str], cwd: Path) -> CommandResult:
        index = len(self.commands) + 1
        stdout_path = self.logs / f"{index:02d}_{name}.stdout.log"
        stderr_path = self.logs / f"{index:02d}_{name}.stderr.log"
        try:
            process = subprocess.run(
                command, cwd=cwd, env=self._env(), stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=1800,
            )
            returncode, stdout, stderr = process.returncode, process.stdout, process.stderr
        except FileNotFoundError as exc:
            returncode, stdout, stderr = 127, b"", str(exc).encode()
        except subprocess.TimeoutExpired as exc:
            returncode, stdout, stderr = 124, exc.stdout or b"", (exc.stderr or b"") + b"\nTIMEOUT"
        stdout_path.write_bytes(stdout)
        stderr_path.write_bytes(stderr)
        row = CommandResult(name, command, str(cwd), returncode, str(stdout_path), str(stderr_path))
        self.commands.append(row)
        return row

    def _record_internal(self, name: str, returncode: int, text: str) -> None:
        index = len(self.commands) + 1
        stdout_path = self.logs / f"{index:02d}_{name}.stdout.log"
        stderr_path = self.logs / f"{index:02d}_{name}.stderr.log"
        stdout_path.write_text(text, encoding="utf-8")
        stderr_path.write_text("" if returncode == 0 else text, encoding="utf-8")
        self.commands.append(CommandResult(name, ["<internal>"], str(self.source), returncode, str(stdout_path), str(stderr_path)))

    def _collect_artifacts(self, result: dict[str, Any]) -> None:
        artifacts = self.output / "artifacts"
        artifacts.mkdir(exist_ok=True)
        zip_path = artifacts / f"{self.cycle_id}-modified-source.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in self.source.rglob("*"):
                if path.is_file() and not any(part in IGNORE for part in path.parts):
                    archive.write(path, path.relative_to(self.source))
        result["artifacts"].append({
            "type": "source", "path": str(zip_path),
            "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
        })
        for pattern in ("maid-ai-bridge/build/libs/*.jar", "control-center/dist/**/*.exe", "agent-core/dist/*"):
            for path in self.source.glob(pattern):
                if not path.is_file():
                    continue
                target = artifacts / path.name
                shutil.copy2(path, target)
                result["artifacts"].append({
                    "type": "build", "path": str(target),
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                })

    @staticmethod
    def _check(command: list[str], cwd: Path) -> None:
        subprocess.run(command, cwd=cwd, check=True, env=HarnessRunner._env(), timeout=120)

    @staticmethod
    def _env() -> dict[str, str]:
        return {
            key: value for key, value in os.environ.items()
            if key in {"PATH", "HOME", "USERPROFILE", "TEMP", "TMP", "SYSTEMROOT", "WINDIR", "JAVA_HOME", "GRADLE_USER_HOME", "LANG"}
        }
