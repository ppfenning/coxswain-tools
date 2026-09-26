import json
import sqlite3
import sys
from pathlib import Path

from agent_tools import cli, remote_fetch, run_store
from agent_tools.cli import main

RUN = "demo-1"
ENDED = "2026-09-25T05:03:46Z"
LINE = f"{RUN}: every task is done in the store; landed on another machine, nothing to fetch"
REAL_FETCH_RUN = remote_fetch.fetch_run
REAL_RUN_TASK_IDS, REAL_TASK_STATE_OF, REAL_INITIATIVE_OF = run_store.run_task_ids, run_store.task_state_of, cli._initiative_of


def _listing(*tasks):
    """What `rsync -r --list-only` prints for a remote `tasks/` holding one record per task in phase p1."""
    return "".join(f"-rw-r--r--             20 2026/09/25 05:00:00 p1/{task}.json\n" for task in tasks)


def _world(tmp_path, monkeypatch, *, mode="store", states=None, listed=None, listing_fails=False, facts=(True, ENDED)):
    """A fetch with every seam faked. The store reports `states`, and the remote lists `listed`, defaulting to the same
    tasks, or fails to list. `fetch_run` records that it was reached, and the runner records each argv.
    The record-skip check `_branch_only_repos` is stubbed out, so every `listings` entry is the landed-elsewhere check's."""
    states = {"a": "done", "b": "done"} if states is None else states
    ws = tmp_path / "chair"
    (ws / "runs").mkdir(parents=True)
    (ws / "runs" / f"{RUN}.remote.json").write_text(json.dumps({"host": "box", "launched_at": "2026-09-25T04:00:00Z"}))
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        f"team: acme\nharness_dir: /opt/harness\nworkspace_dir: {ws}\ncartridges_dir: /opt/cartridges\n"
        f"lane_hosts:\n  - {{name: box, ssh: me@box, workspace_dir: {tmp_path / 'host'}}}\n"
    )
    seen = {"fetch_run": [], "argvs": [], "reads": [], "listings": []}
    listing = None if listing_fails else _listing(*(states if listed is None else listed))
    monkeypatch.setattr(cli.work_state, "work_state_mode", lambda profile: mode)
    monkeypatch.setattr(cli, "_branch_only_repos", lambda *args: None)
    monkeypatch.setattr(cli, "_remote_fetch_facts", lambda runs_dir, run: facts)
    monkeypatch.setattr(cli, "_remote_edge", lambda cwd: (lambda argv: seen["argvs"].append(argv) or 0, lambda path: path))
    monkeypatch.setattr(cli, "_remote_capture", lambda cwd: lambda argv: seen["listings"].append(argv) or listing)
    monkeypatch.setattr(cli, "_initiative_of", lambda work_root, task: "init")
    monkeypatch.setattr(run_store, "run_task_ids", lambda runs_dir, run_id: seen["reads"].append(run_id) or list(states))
    monkeypatch.setattr(run_store, "task_state_of", lambda runs_dir, initiative, task: states[task])
    monkeypatch.setattr(remote_fetch, "fetch_run",
                        lambda *args, **kwargs: seen["fetch_run"].append(1) or remote_fetch.FetchError("verify", "stop"))
    return ws, profile, seen


def _marker(ws):
    path = ws / "runs" / f"{RUN}.fetched.json"
    return json.loads(path.read_text()) if path.exists() else None


def test_an_ended_store_run_with_every_task_done_skips_the_fetch_and_marks_the_run(tmp_path, monkeypatch, capsys):
    ws, profile, seen = _world(tmp_path, monkeypatch)
    assert main(["runs", "fetch", RUN, "--profile", str(profile)]) == 0
    assert (seen["fetch_run"], seen["argvs"]) == ([], [])
    assert {k: v for k, v in _marker(ws).items() if k != "fetched_at"} == {"repos": [], "landed_elsewhere": True}
    assert capsys.readouterr().out.splitlines() == [LINE]


def test_a_live_run_is_refused_as_live_and_never_marked_even_when_every_reported_task_is_done(tmp_path, monkeypatch, capsys):
    ws, profile, seen = _world(tmp_path, monkeypatch, facts=(False, None))
    monkeypatch.setattr(remote_fetch, "fetch_run", REAL_FETCH_RUN)
    assert main(["runs", "fetch", RUN, "--profile", str(profile)]) == 2
    assert _marker(ws) is None
    assert (seen["reads"], seen["listings"], seen["argvs"]) == ([], [], [])
    assert capsys.readouterr().out.startswith(f"fetch: refuse: {RUN} is still live on box")


def test_a_remote_task_the_store_has_no_record_of_takes_the_fetch_path(tmp_path, monkeypatch):
    ws, profile, seen = _world(tmp_path, monkeypatch, listed=["a", "b", "c"])
    assert main(["runs", "fetch", RUN, "--profile", str(profile)]) == 2
    assert (seen["fetch_run"], len(seen["listings"])) == ([1], 1)
    assert _marker(ws) is None


def test_a_remote_that_cannot_be_listed_takes_the_fetch_path_and_is_never_marked(tmp_path, monkeypatch):
    ws, profile, seen = _world(tmp_path, monkeypatch, listing_fails=True)
    assert main(["runs", "fetch", RUN, "--profile", str(profile)]) == 2
    assert (seen["fetch_run"], len(seen["listings"])) == ([1], 1)
    assert _marker(ws) is None


def test_a_task_not_done_takes_the_fetch_path_and_the_remote_is_not_listed(tmp_path, monkeypatch):
    ws, profile, seen = _world(tmp_path, monkeypatch, states={"a": "done", "b": "approved"})
    assert main(["runs", "fetch", RUN, "--profile", str(profile)]) == 2
    assert (seen["fetch_run"], seen["listings"]) == ([1], [])
    assert _marker(ws) is None


def test_files_mode_never_reads_the_store_for_the_skip(tmp_path, monkeypatch):
    ws, profile, seen = _world(tmp_path, monkeypatch, mode="files")
    assert main(["runs", "fetch", RUN, "--profile", str(profile)]) == 2
    assert (seen["fetch_run"], seen["reads"]) == ([1], [])
    assert _marker(ws) is None


def test_fetch_all_prints_the_landed_line_not_fetched_from(tmp_path, monkeypatch, capsys):
    ws, profile, _ = _world(tmp_path, monkeypatch)
    assert main(["runs", "fetch", "--all", "--profile", str(profile)]) == 0
    assert capsys.readouterr().out.splitlines() == [LINE]
    assert main(["runs", "fetch", "--all", "--profile", str(profile)]) == 0
    assert capsys.readouterr().out.splitlines() == ["nothing to fetch"]


def test_fetch_all_still_prints_fetched_from_for_a_run_that_was_pulled(tmp_path, monkeypatch, capsys):
    ws, profile, _ = _world(tmp_path, monkeypatch, states={"a": "approved"})
    monkeypatch.setattr(remote_fetch, "fetch_run", lambda *args, **kwargs: ("/repo",))
    assert main(["runs", "fetch", "--all", "--profile", str(profile)]) == 0
    assert capsys.readouterr().out.splitlines() == [f"{RUN}: fetched from box"]
    assert _marker(ws)["repos"] == ["/repo"]


def test_the_real_store_readers_find_the_run_tasks_and_their_done_states(tmp_path, monkeypatch, capsys):
    ws, profile, seen = _world(tmp_path, monkeypatch)
    monkeypatch.setattr(run_store, "run_task_ids", REAL_RUN_TASK_IDS)
    monkeypatch.setattr(run_store, "task_state_of", REAL_TASK_STATE_OF)
    monkeypatch.setattr(cli, "_initiative_of", REAL_INITIATIVE_OF)
    db = tmp_path / "cox.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE task_records (run_id, phase_id, task_id, record_json)")
    conn.executemany("INSERT INTO task_records VALUES (?, 'p1', ?, '{}')", [(RUN, "a"), (RUN, "b"), ("other-1", "c")])
    conn.execute("CREATE TABLE work_items (initiative, task_id, phase, state, needs_json, updated_at, updated_by)")
    conn.executemany("INSERT INTO work_items VALUES ('init', ?, 'p1', ?, '[]', ?, 'me')",
                     [("a", "done", ENDED), ("b", "done", ENDED), ("c", "approved", ENDED)])
    conn.commit()
    conn.close()
    for task in ("a", "b", "c"):
        item = ws / "work" / "init" / "p1" / f"{task}.md"
        item.parent.mkdir(parents=True, exist_ok=True)
        item.write_text("---\nstate: done\n---\n")
    monkeypatch.setattr(run_store, "_harness_python", lambda: Path(sys.executable))
    monkeypatch.setattr(run_store, "_store_url", lambda runs_dir: f"sqlite:///{db}")
    assert main(["runs", "fetch", RUN, "--profile", str(profile)]) == 0
    assert seen["fetch_run"] == []
    assert capsys.readouterr().out.splitlines() == [LINE]
