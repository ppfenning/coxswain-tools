import pytest
from test_route_cli import _init_repo, _unmeasured_window, _write_harness, _write_launch_profile

from agent_tools import usage_window
from agent_tools.cli import main


@pytest.fixture(autouse=True)
def _no_usage_gather(monkeypatch):
    monkeypatch.setattr(usage_window, "gather", _unmeasured_window)


def test_launch_epic_expands_a_tilde_initiative_into_the_dry_run_argv(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    initiative_dir = ws / "work" / "x"
    initiative_dir.mkdir(parents=True)
    (initiative_dir / "initiative.md").write_text("---\nid: x\ntitle: X\n---\n\nBody\n")
    repo = tmp_path / "repo"
    _init_repo(repo)
    profile = _write_launch_profile(tmp_path, harness_dir, ws)

    rc = main([
        "route", "launch", "epic", "--dry-run",
        "--profile", str(profile),
        "--initiative", "~/workspace/work/x",
        "--repo", str(repo),
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert str(initiative_dir) in out
    assert "~/workspace" not in out
