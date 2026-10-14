import os
import subprocess
import types

import pytest
from test_cli_launch_on import _setup
from test_route_cli import _init_repo, _lint_world, _unmeasured_window, _write_harness, _write_launch_profile
from test_runs_fetch_landed_elsewhere import RUN, _world

from agent_tools import cli, remote_fetch, remote_launch, usage_window
from agent_tools.cli import main


@pytest.fixture(autouse=True)
def _no_usage_gather(monkeypatch):
    monkeypatch.setattr(usage_window, "gather", _unmeasured_window)


def _dry_run_epic(tmp_path, monkeypatch, recorded):
    """A dry-run epic launch of initiative `x` whose initiative.md records `recorded`, with HOME at `tmp_path`."""
    monkeypatch.setenv("HOME", str(tmp_path))
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    initiative_dir = ws / "work" / "x"
    initiative_dir.mkdir(parents=True)
    (initiative_dir / "initiative.md").write_text(f"---\nid: x\ntitle: X\nrepo: {recorded}\n---\n\nBody\n")
    _init_repo(tmp_path / "repo")
    profile = _write_launch_profile(tmp_path, harness_dir, ws)
    return main(
        ["route", "launch", "epic", "--dry-run", "--profile", str(profile), "--initiative", str(initiative_dir)]
    )


def test_launch_epic_expands_a_tilde_repo_for_the_precheck_and_the_argv(tmp_path, monkeypatch, capsys):
    rc = _dry_run_epic(tmp_path, monkeypatch, "~/repo")
    out = capsys.readouterr().out
    assert rc == 0
    assert str(tmp_path / "repo") in out
    assert "~/repo" not in out


def test_launch_epic_expands_a_repo_recorded_under_another_users_home(tmp_path, monkeypatch, capsys):
    rc = _dry_run_epic(tmp_path, monkeypatch, "/home/user/repo")
    out = capsys.readouterr().out
    assert rc == 0
    assert str(tmp_path / "repo") in out
    assert "/home/user" not in out


def test_lint_refuses_an_absolute_path_under_home_and_names_the_tilde_form(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    phase = tmp_path / "work" / "demo" / "p1"
    phase.mkdir(parents=True)
    (phase / "t1.md").write_text(
        f"---\nid: t1\ntitle: One\nphase: p1\nstate: ready\nsurfaces: []\n---\nRead {tmp_path}/notes/spec.md first.\n",
    )
    rc = main(["route", "lint", str(tmp_path / "work" / "demo")])
    out = capsys.readouterr().out
    assert rc == 2
    assert any(line.startswith("t1 home_path:") and "'~/notes/spec.md'" in line for line in out.splitlines())


def test_fetch_edge_hands_the_home_to_fetch_run(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _, profile, _ = _world(tmp_path, monkeypatch, states={"a": "approved"})
    seen = []
    monkeypatch.setattr(
        remote_fetch, "fetch_run", lambda *args, **kwargs: seen.append(kwargs.get("home")) or ("/repo",)
    )
    assert main(["runs", "fetch", RUN, "--profile", str(profile)]) == 0
    assert seen == [str(tmp_path)]


@pytest.mark.parametrize("recorded", ["~/coxswain-tools", "/home/user/coxswain-tools"])
def test_lint_reads_the_checkout_of_a_recorded_home_repo(tmp_path, monkeypatch, capsys, recorded):
    monkeypatch.setenv("HOME", str(tmp_path))
    initiative_dir, _ = _lint_world(tmp_path, ["agent_tools/b.py"])
    rc = main(["route", "lint", str(initiative_dir), "--repo", recorded])
    out = capsys.readouterr().out
    assert rc == 2
    assert "t1 surface_missing: t1 surfaces agent_tools/b.py, which is not in the repository" in out


def _live_launch(tmp_path, monkeypatch, *extra):
    """A live `route launch epic` with the repo recorded as `~/repo`; returns (rc, [(argv, env)] of detached starts)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_host_auth_output", lambda argv: '{"loggedIn": true}')
    _, _, argv = _setup(tmp_path)
    argv[argv.index("--repo") + 1] = "~/repo"
    started = []
    real = subprocess.Popen

    def popen(*args, **kwargs):
        if not kwargs.get("start_new_session"):
            return real(*args, **kwargs)
        started.append((args[0], kwargs["env"]))
        return types.SimpleNamespace(pid=os.getpid())

    monkeypatch.setattr(cli.subprocess, "Popen", popen)
    return main([*argv, *extra]), started


def test_local_launch_hands_the_harness_the_expanded_repo_in_argv_and_env(tmp_path, monkeypatch):
    rc, started = _live_launch(tmp_path, monkeypatch)
    assert rc == 0
    [(argv, env)] = started
    assert str(tmp_path / "repo") in argv
    assert "~/repo" not in argv
    assert env["PATH"].startswith(f"{tmp_path / 'repo'}/.venv/bin")


def test_lane_host_launch_passes_the_recorded_repo_and_the_home_to_remote_launch(tmp_path, monkeypatch):
    calls = []

    def launch_on_host(*args, **kwargs):
        calls.append((args, kwargs))
        return {"host": "box", "launched_at": "2026-10-14T00:00:00Z", "repo": args[7]}

    monkeypatch.setattr(remote_launch, "launch_on_host", launch_on_host)
    rc, started = _live_launch(tmp_path, monkeypatch, "--on", "box")
    assert rc == 0
    assert started == []
    [(args, kwargs)] = calls
    assert args[7] == "~/repo"
    assert kwargs["home"] == str(tmp_path)
