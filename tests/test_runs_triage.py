import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_tools import run_store
from agent_tools.runs_triage import (
    Attempt,
    GitMainReader,
    StoreTriageReader,
    SurfaceStatus,
    TriageError,
    main_lookup_argv,
    pick_newest_run,
    refusal_row,
    render_json,
    render_text,
    run,
    surface_status,
    tail_lines,
    triage_task,
)

OUTPUT_25 = "\n".join(f"out {i}" for i in range(1, 26))

FULL_RECORD = {
    "initiative": "init-a",
    "phase": "p1",
    "state": "quarantined",
    "surfaces": ["agent_tools/a.py", "agent_tools/b.py", "agent_tools/c.py (new)"],
    "review": {"verdict": "revise", "objection": "no test for the empty row"},
    "failed_check": {"command": "pytest -q", "output": OUTPUT_25},
}

ATTEMPTS = [
    {"seq": 2, "kind": "build", "cause": "review_revise", "reason": "adversary objected"},
    {"seq": 1, "kind": "build", "cause": "check_failed", "reason": "pytest failed"},
]

REFUSAL = {"time": "2026-10-16T10:00:00Z", "head": "abc123", "reason": "main moved"}


class StubStore:
    def __init__(self, records, attempts=(), run_id=None, refusals=None):
        self.records = records
        self._attempts = attempts
        self.run_id = run_id
        self.refusals = refusals or {}

    def task_record(self, task_id):
        return self.records.get(task_id)

    def attempts(self, task_id):
        return list(self._attempts)

    def newest_run_id(self, task_id):
        return self.run_id

    def land_refusal(self, phase):
        return self.refusals.get(phase)


class StubGit:
    def __init__(self, on_main):
        self.on_main = set(on_main)

    def path_exists_on_main(self, path):
        return path in self.on_main


def full_triage():
    store = StubStore({"t-7": FULL_RECORD}, ATTEMPTS, "run-9", {"p1": REFUSAL})
    return triage_task("t-7", store, StubGit({"agent_tools/a.py"}))


def test_full_text():
    assert render_text(full_triage()) == (
        "task t-7  initiative=init-a  phase=p1  state=quarantined  newest_run=run-9\n"
        "attempts:\n"
        "  1 build cause=check_failed reason=pytest failed\n"
        "  2 build cause=review_revise reason=adversary objected\n"
        "review: revise\n"
        "  objection: no test for the empty row\n"
        "failed check: pytest -q\n"
        "  out 6\n  out 7\n  out 8\n  out 9\n  out 10\n  out 11\n  out 12\n  out 13\n"
        "  out 14\n  out 15\n  out 16\n  out 17\n  out 18\n  out 19\n  out 20\n  out 21\n"
        "  out 22\n  out 23\n  out 24\n  out 25\n"
        "land refusal: time=2026-10-16T10:00:00Z head=abc123\n"
        "  reason: main moved\n"
        "surfaces:\n"
        "  present agent_tools/a.py\n"
        "  missing agent_tools/b.py\n"
        "  new agent_tools/c.py"
    )


def test_full_json():
    assert render_json(full_triage()) == (
        '{"task_id": "t-7", "initiative": "init-a", "phase": "p1", "state": "quarantined", '
        '"newest_run_id": "run-9", "attempts": ['
        '{"seq": 1, "kind": "build", "cause": "check_failed", "reason": "pytest failed"}, '
        '{"seq": 2, "kind": "build", "cause": "review_revise", "reason": "adversary objected"}], '
        '"review": {"verdict": "revise", "objection": "no test for the empty row"}, '
        '"failed_check": {"command": "pytest -q", "output_tail": '
        '"out 6\\nout 7\\nout 8\\nout 9\\nout 10\\nout 11\\nout 12\\nout 13\\nout 14\\nout 15\\n'
        'out 16\\nout 17\\nout 18\\nout 19\\nout 20\\nout 21\\nout 22\\nout 23\\nout 24\\nout 25"}, '
        '"land_refusal": {"time": "2026-10-16T10:00:00Z", "head": "abc123", "reason": "main moved"}, '
        '"surfaces": [{"path": "agent_tools/a.py", "status": "present"}, '
        '{"path": "agent_tools/b.py", "status": "missing"}, '
        '{"path": "agent_tools/c.py", "status": "new"}]}'
    )


def test_unknown_task_is_a_value():
    result = triage_task("nope", StubStore({}), StubGit(()))
    assert result == TriageError("nope", "unknown task id: nope")


def empty_triage():
    record = {"initiative": "init-b", "phase": "p2", "state": "planned", "surfaces": []}
    return triage_task("t-1", StubStore({"t-1": record}), StubGit(()))


def test_empty_blocks_text():
    assert render_text(empty_triage()) == (
        "task t-1  initiative=init-b  phase=p2  state=planned  newest_run=-\n"
        "attempts: none recorded\n"
        "review: none recorded\n"
        "failed check: none recorded\n"
        "land refusal: none recorded\n"
        "surfaces: none listed"
    )


def test_empty_blocks_json():
    assert render_json(empty_triage()) == (
        '{"task_id": "t-1", "initiative": "init-b", "phase": "p2", "state": "planned", '
        '"newest_run_id": null, "attempts": [], "review": null, "failed_check": null, '
        '"land_refusal": null, "surfaces": []}'
    )


def test_tail_keeps_last_twenty_lines():
    assert tail_lines(OUTPUT_25).splitlines() == [f"out {i}" for i in range(6, 26)]


def test_tail_keeps_short_output_whole():
    assert tail_lines("a\nb\nc") == "a\nb\nc"


def test_surface_status_rules():
    assert surface_status("x.py (new)", False) == SurfaceStatus("x.py", "new")
    assert surface_status("x.py (new)", True) == SurfaceStatus("x.py", "present")
    assert surface_status("x.py", True) == SurfaceStatus("x.py", "present")
    assert surface_status("x.py", False) == SurfaceStatus("x.py", "missing")


def test_run_text_and_json_use_the_stub_readers():
    store, git = StubStore({"t-7": FULL_RECORD}, ATTEMPTS, "run-9", {"p1": REFUSAL}), StubGit({"agent_tools/a.py"})
    assert run("t-7", False, store=store, git=git) == (0, render_text(full_triage()))
    code, out = run("t-7", True, store=store, git=git)
    assert (code, json.loads(out)["newest_run_id"]) == (0, "run-9")


def test_run_unknown_task_is_nonzero_with_one_line():
    code, out = run("nope", False, store=StubStore({}), git=StubGit([]))
    assert (code, out) == (1, "unknown task id: nope")


def test_newest_run_is_by_parsed_time_not_text():
    rows = [
        {"run_id": "run-x", "started_at": "2026-10-17T01:00:00Z", "launched_at": None},
        {"run_id": "run-y", "started_at": "2026-10-17T05:30:00+05:00", "launched_at": None},
    ]
    # run-y is 00:30 UTC but its text sorts after run-x (01:00 UTC): text order and time order disagree.
    assert max(rows, key=lambda r: r["started_at"])["run_id"] == "run-y"
    assert pick_newest_run(rows) == "run-x"
    assert pick_newest_run(list(reversed(rows))) == "run-x"
    later = {"run_id": "run-c", "started_at": None, "launched_at": "2026-10-17T02:00:00+00:00"}
    assert pick_newest_run([*rows, later]) == "run-c"
    assert pick_newest_run([]) is None


RUN_1 = {"review": {"verdict": "approve"}}
RUN_2 = {
    "review": {"verdict": "revise"},
    "arbitration": {"reasoning": "No test for the empty row."},
    "build": {
        "commands_run": [
            {"command": "ruff check", "output": "ok", "exit_code": 0},
            {"command": "pytest -q", "output": "1 failed", "exit_code": 1},
            {"command": "git status", "output": "clean", "source": "trace"},
        ]
    },
}


@pytest.fixture
def sqlite_store(monkeypatch, tmp_path):
    path = tmp_path / "cox.db"
    seed = sqlite3.connect(path)
    seed.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, launched_at TEXT, started_at TEXT)")
    seed.execute("CREATE TABLE task_records (run_id TEXT, phase_id TEXT, task_id TEXT, record_json TEXT)")
    seed.execute(
        "CREATE TABLE attempts (run_id TEXT, task_id TEXT, phase_id TEXT, seq INTEGER, kind TEXT, reason TEXT)"
    )
    seed.executemany(
        "INSERT INTO runs VALUES (?, ?, ?)",
        [
            ("run-1", "2026-10-17T00:50:00Z", "2026-10-17T05:30:00+05:00"),
            ("run-2", "2026-10-17T01:10:00+00:00", "2026-10-17T01:30:00+00:00"),
            ("run-3", None, "2026-10-18T00:00:00Z"),
        ],
    )
    seed.executemany(
        "INSERT INTO task_records VALUES (?, ?, ?, ?)",
        [
            ("run-1", "p1", "t-7", json.dumps(RUN_1)),
            ("run-2", "p1", "t-7", json.dumps(RUN_2)),
            ("run-3", "p1", "t-8", "{}"),
        ],
    )
    seed.executemany(
        "INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("run-1", "t-7", "p1", 1, "build", "first"),
            ("run-2", "t-7", "p1", 2, "build", "second"),
            ("run-3", "t-8", "p1", 1, "build", "other task"),
        ],
    )
    seed.commit()
    seed.close()

    def opened(_runs_dir):
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn, "?"

    monkeypatch.setattr(run_store, "_open", opened)
    return tmp_path


def sqlite_reader(runs_dir, rows=()):
    item = {"task_id": "t-7", "initiative": "init-a", "phase": "p1", "state": "quarantined", "surfaces": '["a.py"]'}
    return StoreTriageReader(runs_dir, actions_reader=lambda _dir: list(rows), queue_reader=lambda *_a: [item])


def test_store_reader_runs_its_sql_against_sqlite(sqlite_store):
    reader = sqlite_reader(sqlite_store)
    assert reader.newest_run_id("t-7") == "run-2"
    assert reader.newest_run_id("t-8") == "run-3"
    assert [a["reason"] for a in reader.attempts("t-7")] == ["first", "second"]
    record = reader.task_record("t-7")
    assert (record["initiative"], record["phase"], record["state"], record["surfaces"]) == (
        "init-a",
        "p1",
        "quarantined",
        ["a.py"],
    )
    assert record["review"] == {"verdict": "revise", "objection": "No test for the empty row."}
    assert record["failed_check"] == {"command": "pytest -q", "output": "1 failed"}
    assert reader.unreadable is False


def test_run_reads_a_sqlite_store_end_to_end(sqlite_store):
    rows = [land_row("2026-10-16T10:00:00Z", "init-a", "p1", "refused", "main moved")]
    code, out = run("t-7", True, store=sqlite_reader(sqlite_store, rows), git=StubGit([]))
    doc = json.loads(out)
    assert (code, doc["newest_run_id"], doc["land_refusal"]["reason"]) == (0, "run-2", "main moved")
    assert doc["surfaces"] == [{"path": "a.py", "status": "missing"}]


def test_unreadable_store_is_named_in_the_unknown_task_message(monkeypatch, tmp_path):
    monkeypatch.setattr(run_store, "_open", lambda _runs_dir: None)
    reader = StoreTriageReader(tmp_path, actions_reader=lambda _dir: [], queue_reader=lambda *_a: [])
    code, out = run("t-7", False, store=reader, git=StubGit([]))
    assert code == 1 and "\n" not in out
    assert out.startswith("unknown task id: t-7") and "could not be read" in out


def test_land_refusal_needs_a_task_record_first():
    reader = StoreTriageReader(Path("."), actions_reader=lambda _dir: [], queue_reader=lambda *_a: [])
    assert reader.land_refusal("p1") is None


def land_row(ts, initiative, phase, status, reason=""):
    doc = {"kind": "land_phase", "initiative": initiative, "phase": phase, "run": "r1", "status": status, "head": "h1"}
    return {"ts": ts, "kind": "land_phase", "target": initiative, "action_json": json.dumps({**doc, "reason": reason})}


def test_refusal_row_keeps_the_initiative_and_phase():
    rows = [
        land_row("2026-10-16T10:00:00Z", "init-a", "p1", "refused", "main moved"),
        land_row("2026-10-16T11:00:00Z", "init-a", "p2", "refused", "other phase"),
        land_row("2026-10-16T12:00:00Z", "init-b", "p1", "refused", "other initiative"),
    ]
    assert refusal_row(rows, "init-a", "p1") == {"time": "2026-10-16T10:00:00Z", "head": "h1", "reason": "main moved"}
    assert refusal_row(rows, "init-a", "p9") is None


def test_store_reader_land_refusal_uses_stub_rows_without_a_store(monkeypatch, tmp_path):
    monkeypatch.setattr(run_store, "_open", lambda _runs_dir: None)
    rows = [land_row("2026-10-16T10:00:00Z", "init-a", "p1", "refused", "main moved")]
    item = {
        "kind": "task",
        "task_id": "t-7",
        "initiative": "init-a",
        "phase": "p1",
        "state": "quarantined",
        "surfaces": ["a.py"],
    }
    reader = StoreTriageReader(tmp_path, actions_reader=lambda _dir: rows, queue_reader=lambda *_a: [item])
    assert reader.task_record("t-7")["surfaces"] == ["a.py"]
    assert reader.land_refusal("p1")["reason"] == "main moved"
    assert reader.attempts("t-7") == [] and reader.newest_run_id("t-7") is None


def test_git_reader_builds_a_read_only_main_lookup():
    calls = []

    def runner(argv, **_kw):
        calls.append(argv)
        return SimpleNamespace(returncode=0 if argv[3] != "rev-parse" or argv[-1].startswith("main") else 1)

    assert GitMainReader("/repo", runner).path_exists_on_main("agent_tools/a.py") is True
    assert calls[-1] == main_lookup_argv("/repo", "main", "agent_tools/a.py")
    assert calls[-1][3:5] == ["cat-file", "-e"] and calls[-1][-1] == "main:agent_tools/a.py"
    assert not {"checkout", "switch", "restore", "worktree"} & {a for c in calls for a in c}
    assert GitMainReader("/repo", lambda argv, **_kw: SimpleNamespace(returncode=1)).path_exists_on_main("x") is False


def test_attempts_sorted_by_seq():
    assert full_triage().attempts == (
        Attempt(1, "build", "check_failed", "pytest failed"),
        Attempt(2, "build", "review_revise", "adversary objected"),
    )
