from __future__ import annotations

from pathlib import Path

import pytest

from agent_tools import cli


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[dict]:
    calls: list[dict] = []

    def fake_run(task_id: str, as_json: bool, **kwargs) -> tuple[int, str]:
        calls.append({"task_id": task_id, **kwargs})
        return 0, "triaged"

    monkeypatch.setattr(cli.runs_triage, "run", fake_run)
    monkeypatch.delenv("AGENT_TOOLS_PROFILE", raising=False)
    monkeypatch.setattr(cli, "DEFAULT_PROFILE", str(tmp_path / "no-default-profile.yaml"))
    return calls


def test_profile_gives_the_workspace_runs_folder_and_repo_flag_gives_the_repo(seen: list[dict], tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"workspace_dir: {tmp_path}/ws\n")
    repo = tmp_path / "task-repo"
    assert cli.main(["runs", "triage", "T-1", "--profile", str(profile), "--repo", str(repo)]) == 0
    assert seen == [{"task_id": "T-1", "runs_dir": tmp_path / "ws" / "runs", "repo": repo}]


def test_no_profile_and_no_flag_keeps_the_current_directory(
    seen: list[dict], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli.main(["runs", "triage", "T-1"]) == 0
    assert seen == [{"task_id": "T-1", "runs_dir": None, "repo": Path(".")}]


def test_missing_named_profile_exits_2_without_running(seen: list[dict], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "missing.yaml"
    assert cli.main(["runs", "triage", "T-1", "--profile", str(missing)]) == 2
    assert capsys.readouterr().out == f"routing: no profile at {missing}\n"
    assert seen == []
