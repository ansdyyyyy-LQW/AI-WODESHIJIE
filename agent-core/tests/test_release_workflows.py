from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_release_workflows_use_current_root_versions_and_release_contract() -> None:
    linux = (ROOT / ".github" / "workflows" / "maid-ai-ci.yml").read_text(encoding="utf-8")
    windows = (ROOT / ".github" / "workflows" / "maid-ai-windows.yml").read_text(encoding="utf-8")
    combined = linux + "\n" + windows

    assert "maid-ai-project/" not in combined
    assert "MaidAI-Bridge-0.1.0.jar" not in combined
    assert "MaidAI-Windows-0.1.0" not in combined
    assert "--release-check" in windows
    assert "--skip-tests" not in windows
    assert "node-version: '24.14.1'" in windows
    assert "version: '11.19.0'" in windows
    assert "MaidAI-Windows-0.3.0-Release" in windows
    release_step = windows.split("- name: Upload Windows release ZIP and validation", 1)[1]
    release_step = release_step.split("- name: Upload failed Windows validation", 1)[0]
    assert "if: success()" in release_step
    assert "if: always()" not in release_step
    assert "MaidAI-Windows-0.3.0-Failed-Validation" in windows
