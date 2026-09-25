import json
import shutil
import sqlite3
import subprocess

import pytest

from agent_tools import cli
from agent_tools.cli import main

needs_tools = pytest.mark.skipif(
    shutil.which("rsync") is None or shutil.which("git") is None, reason="rsync and git are required"
)

RUN = "demo-1"
ENDED = "2026-09-25T05:03:46Z"


def _git(cwd, *args):
    ident = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
    return subprocess.run(["git", *ident, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def _store(runs_dir, ended_at=ENDED, lease_expires=None):
    """A sqlite store holding one run row, and a lease row for it when `lease_expires` is given."""
    conn = sqlite3.connect(runs_dir / "cox.db")
    conn.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, launched_at TEXT, ended_at TEXT)")
    conn.execute("INSERT INTO runs VALUES (?, ?, ?)", (RUN, "2026-09-25T04:00:00Z", ended_at))
    conn.execute("CREATE TABLE leases (name TEXT PRIMARY KEY, holder TEXT, epoch INTEGER, heartbeat_at TEXT, expires_at TEXT)")
    if lease_expires:
        conn.execute("INSERT INTO leases VALUES ('runs:demo', ?, 1, '2026-09-25T04:59:00Z', ?)", (RUN, lease_expires))
    conn.commit()
    conn.close()


def _world(tmp_path, monkeypatch, *, remote_record=True, host_in_profile=True, **store):
    """A chair workspace and a host directory that is a local path; the host has `repo` with branch `agents/demo-1/t`."""
    ws, host_ws = tmp_path / "chair", tmp_path / "host"
    (ws / "runs").mkdir(parents=True)
    host_repo, chair_repo = host_ws / "repo", ws / "repo"
    for repo in (host_repo, chair_repo):
        repo.mkdir(parents=True)
        _git(repo, "init", "-q", "-b", "main")
    (host_repo / "f.txt").write_text("x")
    _git(host_repo, "add", "f.txt")
    _git(host_repo, "commit", "-q", "-m", "one")
    _git(host_repo, "branch", f"agents/{RUN}/t")
    task = host_ws / "runs" / RUN / "tasks" / "p1" / "t.json"
    task.parent.mkdir(parents=True)
    task.write_text(json.dumps({"repo": str(host_repo)}))
    (host_ws / "runs" / f"{RUN}.log").write_text("log")
    if remote_record:
        (ws / "runs" / f"{RUN}.remote.json").write_text(json.dumps({"host": "box", "launched_at": "2026-09-25T04:00:00Z"}))
    hosts = f"lane_hosts:\n  - {{name: box, ssh: me@box, workspace_dir: {host_ws}}}\n" if host_in_profile else ""
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        f"team: acme\nharness_dir: /opt/harness\nworkspace_dir: {ws}\n"
        "cartridges_dir: /opt/cartridges\nprovider_profile: /opt/providers/acme.yaml\n" + hosts
    )
    _store(ws / "runs", **store)
    monkeypatch.setattr(cli, "_remote_edge", lambda cwd: (lambda argv: subprocess.run(argv, cwd=cwd).returncode, lambda path: path))
    return ws, profile, chair_repo


@needs_tools
def test_fetch_prints_what_arrived_and_brings_the_run_and_its_branch(tmp_path, monkeypatch, capsys):
    ws, profile, chair_repo = _world(tmp_path, monkeypatch, lease_expires="2026-09-25T04:59:30Z")
    rc = main(["runs", "fetch", RUN, "--profile", str(profile)])
    out = capsys.readouterr().out.splitlines()
    assert rc == 0
    assert out == [f"run {RUN}", "host box", f"repo {chair_repo}"]
    assert (ws / "runs" / RUN / "tasks" / "p1" / "t.json").exists()
    assert (ws / "runs" / f"{RUN}.log").read_text() == "log"
    assert f"agents/{RUN}/t" in _git(chair_repo, "branch", "--list")


def test_fetch_with_no_remote_record_exits_non_zero_and_names_the_file(tmp_path, monkeypatch, capsys):
    _, profile, _ = _world(tmp_path, monkeypatch, remote_record=False)
    rc = main(["runs", "fetch", RUN, "--profile", str(profile)])
    assert rc == 2
    assert f"fetch: no {RUN}.remote.json" in capsys.readouterr().out


def test_fetch_when_the_host_left_the_profile_exits_non_zero_and_names_it(tmp_path, monkeypatch, capsys):
    ws, profile, _ = _world(tmp_path, monkeypatch, host_in_profile=False)
    rc = main(["runs", "fetch", RUN, "--profile", str(profile)])
    assert rc == 2
    assert "fetch: host box is no longer in the profile lane_hosts" in capsys.readouterr().out
    assert not (ws / "runs" / RUN).exists()


def test_fetch_of_a_live_lane_exits_non_zero_and_copies_nothing(tmp_path, monkeypatch, capsys):
    ws, profile, _ = _world(tmp_path, monkeypatch, lease_expires="2999-01-01T00:00:00Z")
    rc = main(["runs", "fetch", RUN, "--profile", str(profile)])
    assert rc == 2
    assert f"fetch: refuse: {RUN} is still live on box" in capsys.readouterr().out
    assert not (ws / "runs" / RUN).exists()


def test_land_of_a_remote_run_with_no_task_records_says_to_fetch_first(tmp_path, monkeypatch, capsys):
    ws, profile, chair_repo = _world(tmp_path, monkeypatch)
    rc = main(["runs", "land", RUN, "--repo", str(chair_repo), "--profile", str(profile)])
    assert rc == 2
    assert capsys.readouterr().out.strip() == f"land: {RUN} is a remote run; run cox runs fetch {RUN}, then cox runs land {RUN} again"


def test_land_of_a_remote_run_whose_task_records_are_here_carries_on(tmp_path, monkeypatch, capsys):
    ws, profile, chair_repo = _world(tmp_path, monkeypatch)
    task = ws / "runs" / RUN / "tasks" / "p1" / "t.json"
    task.parent.mkdir(parents=True)
    task.write_text(json.dumps({"repo": str(chair_repo), "initiative": "demo", "phase": "p1", "task": "t", "run": RUN}))
    main(["runs", "land", RUN, "--repo", str(chair_repo), "--profile", str(profile)])
    assert "remote run" not in capsys.readouterr().out
