import json
import shutil
import subprocess

import pytest

from agent_tools import cli, run_store
from agent_tools.cli import main

RUN = "demo-1"
ENDED = "2026-09-25T05:03:46Z"
LISTING = (
    "drwxr-xr-x          4,096 2026/09/25 05:00:00 .\n"
    "drwxr-xr-x          4,096 2026/09/25 05:00:00 p1\n"
    "-rw-r--r--             20 2026/09/25 05:00:00 p1/a.json\n"
    "-rw-r--r--             20 2026/09/25 05:00:00 p1/b.json\n"
)


def _world(tmp_path, monkeypatch, *, mode, stored=("a", "b"), listing=LISTING, remote_repo=None):
    """A fetch with every seam faked: the runner records argvs, the store holds `stored` tasks, the remote lists `listing`."""
    ws, host_ws = tmp_path / "chair", tmp_path / "host"
    (ws / "runs").mkdir(parents=True)
    record = {"host": "box", "launched_at": "2026-09-25T04:00:00Z"}
    (ws / "runs" / f"{RUN}.remote.json").write_text(json.dumps(record if remote_repo is None else {**record, "repo": remote_repo}))
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        f"team: acme\nharness_dir: /opt/harness\nworkspace_dir: {ws}\ncartridges_dir: /opt/cartridges\n"
        f"lane_hosts:\n  - {{name: box, ssh: me@box, workspace_dir: {host_ws}}}\n"
    )
    argvs, reads = [], []

    def task_record(runs_dir, run_id, phase, task):
        reads.append((phase, task))
        return {"repo": str(host_ws / "repo")} if task in stored else None

    monkeypatch.setattr(cli, "_remote_edge", lambda cwd: (lambda argv: argvs.append(argv) or 0, lambda path: path))
    monkeypatch.setattr(cli, "_remote_capture", lambda cwd: lambda argv: listing)
    monkeypatch.setattr(cli, "_remote_fetch_facts", lambda runs_dir, run: (True, ENDED))
    monkeypatch.setattr(cli.work_state, "work_state_mode", lambda profile: mode)
    monkeypatch.setattr(run_store, "task_record", task_record)
    return ws, profile, argvs, reads


def _copies_records(argvs, ws):
    """True when the run-directory rsync is the plain copy, with no `tasks/` exclusion."""
    pulls = [a for a in argvs if a[0] == "rsync" and a[-1] == f"{ws}/runs/{RUN}/"]
    assert len(pulls) == 1
    return not any("tasks" in part for part in pulls[0])


def _branch_fetches(argvs):
    return [a for a in argvs if a[0] == "git" and "fetch" in a]


def test_store_complete_skips_the_record_copy_and_fetches_branches(tmp_path, monkeypatch, capsys):
    ws, profile, argvs, _ = _world(tmp_path, monkeypatch, mode="store")
    assert main(["runs", "fetch", RUN, "--profile", str(profile)]) == 0
    assert not _copies_records(argvs, ws)
    assert [a[2] for a in _branch_fetches(argvs)] == [str(ws / "repo")]
    assert capsys.readouterr().out.splitlines() == [
        f"{RUN}: task records skipped, the store holds them", f"run {RUN}", "host box", f"repo {ws / 'repo'}",
    ]


def test_store_partial_fetches_records_and_branches(tmp_path, monkeypatch, capsys):
    ws, profile, argvs, reads = _world(tmp_path, monkeypatch, mode="store", stored=("a",), remote_repo=str(tmp_path / "host" / "repo"))
    assert main(["runs", "fetch", RUN, "--profile", str(profile)]) == 0
    assert reads == [("p1", "a"), ("p1", "b")]
    assert _copies_records(argvs, ws)
    assert len(_branch_fetches(argvs)) == 1
    assert "skipped" not in capsys.readouterr().out


def test_files_mode_fetches_both_and_never_reads_the_store(tmp_path, monkeypatch, capsys):
    ws, profile, argvs, reads = _world(tmp_path, monkeypatch, mode="files", remote_repo=str(tmp_path / "host" / "repo"))
    assert main(["runs", "fetch", RUN, "--profile", str(profile)]) == 0
    assert reads == []
    assert _copies_records(argvs, ws)
    assert capsys.readouterr().out.splitlines() == [f"run {RUN}", "host box", f"repo {ws / 'repo'}"]


@pytest.mark.parametrize("listing", ["", None])
def test_an_unlistable_remote_counts_as_incomplete(tmp_path, monkeypatch, listing):
    ws, profile, argvs, reads = _world(tmp_path, monkeypatch, mode="store", listing=listing, remote_repo=str(tmp_path / "host" / "repo"))
    assert main(["runs", "fetch", RUN, "--profile", str(profile)]) == 0
    assert reads == []
    assert _copies_records(argvs, ws)


def test_an_unreadable_store_counts_as_incomplete(tmp_path, monkeypatch):
    ws, profile, argvs, _ = _world(tmp_path, monkeypatch, mode="store", stored=(), remote_repo=str(tmp_path / "host" / "repo"))
    assert main(["runs", "fetch", RUN, "--profile", str(profile)]) == 0
    assert _copies_records(argvs, ws)


def test_a_raising_store_read_counts_as_incomplete(tmp_path, monkeypatch):
    ws, profile, argvs, _ = _world(tmp_path, monkeypatch, mode="store", remote_repo=str(tmp_path / "host" / "repo"))

    def boom(*args):
        raise OSError("store down")

    monkeypatch.setattr(run_store, "task_record", boom)
    assert main(["runs", "fetch", RUN, "--profile", str(profile)]) == 0
    assert _copies_records(argvs, ws)


def test_store_complete_with_no_repo_anywhere_copies_the_records(tmp_path, monkeypatch):
    ws, profile, argvs, _ = _world(tmp_path, monkeypatch, mode="store")
    monkeypatch.setattr(run_store, "task_record", lambda *args: {})
    assert main(["runs", "fetch", RUN, "--profile", str(profile)]) == 2
    assert _copies_records(argvs, ws)


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_the_listing_reads_what_rsync_prints_for_a_local_tasks_dir(tmp_path):
    (tmp_path / "tasks" / "p1").mkdir(parents=True)
    (tmp_path / "tasks" / "p1" / "a.json").write_text("{}")
    listing = subprocess.run(["rsync", "-r", "--list-only", f"{tmp_path}/tasks/"], capture_output=True, text=True).stdout
    assert cli._task_pairs_from_listing(listing) == [("p1", "a")]


def test_the_listing_yields_phase_and_task_of_each_record_file():
    assert cli._task_pairs_from_listing(LISTING) == [("p1", "a"), ("p1", "b")]


def test_the_store_holds_every_record_only_when_each_pair_reads_and_there_is_one():
    pairs = [("p1", "a"), ("p1", "b")]
    assert cli._store_holds_every_record(pairs, lambda phase, task: {"repo": "r"})
    assert not cli._store_holds_every_record(pairs, lambda phase, task: None if task == "b" else {})
    assert not cli._store_holds_every_record([], lambda phase, task: {})
