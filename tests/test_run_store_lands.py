import json
import sqlite3

from agent_tools import run_store

SINCE, UNTIL = "2026-10-01T00:00:00+00:00", "2026-10-08T00:00:00+00:00"
INSIDE = "2026-10-03T12:00:00+00:00"


def store(runs_dir, *items):
    conn = sqlite3.connect(runs_dir / "cox.db")
    conn.execute("CREATE TABLE work_items (initiative TEXT, task_id TEXT, phase TEXT, state TEXT, needs_json TEXT, updated_at TEXT, updated_by TEXT)")
    for initiative, task, state, updated_at in items:
        conn.execute("INSERT INTO work_items (initiative, task_id, state, updated_at) VALUES (?, ?, ?, ?)", (initiative, task, state, updated_at))
    conn.commit()
    conn.close()


def land_log(runs_dir, *lands):
    rows = [{"ts": ts, "run": run, "task": task, "steps_reached": ["push", "mark_done"], "exit": 0, "pr": None} for run, task, ts in lands]
    (runs_dir / "land.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_done_work_items_win_over_land_records(tmp_path):
    store(tmp_path, ("epic", "a", "done", INSIDE))
    land_log(tmp_path, ("epic-1", "b", INSIDE))
    got = run_store.landed_tasks(tmp_path, SINCE, UNTIL)
    assert (got.source, got.rows) == ("work_items", ({"run_id": "epic", "task_id": "a", "ts": INSIDE},))


def test_no_done_work_item_in_the_window_falls_back_to_land_records_and_says_so(tmp_path):
    store(tmp_path, ("epic", "a", "ready", INSIDE), ("epic", "c", "done", "2026-09-01T00:00:00+00:00"))
    land_log(tmp_path, ("epic-1", "b", INSIDE))
    got = run_store.landed_tasks(tmp_path, SINCE, UNTIL)
    assert (got.source, got.rows) == ("land_records", ({"run_id": "epic-1", "task_id": "b", "ts": INSIDE},))


def test_a_task_in_both_sources_is_counted_once_and_a_repeated_land_keeps_its_first_ts(tmp_path):
    store(tmp_path, ("epic", "a", "done", INSIDE))
    land_log(tmp_path, ("epic", "a", INSIDE))
    assert len(run_store.landed_tasks(tmp_path, SINCE, UNTIL).rows) == 1
    (tmp_path / "cox.db").unlink()
    land_log(tmp_path, ("epic-1", "a", "2026-10-05T00:00:00+00:00"), ("epic-1", "a", INSIDE))
    assert run_store.landed_tasks(tmp_path, SINCE, UNTIL).rows == ({"run_id": "epic-1", "task_id": "a", "ts": INSIDE},)


def test_the_window_includes_since_and_excludes_until_in_both_sources(tmp_path):
    store(tmp_path, ("epic", "at-since", "done", SINCE), ("epic", "at-until", "done", UNTIL))
    assert [r["task_id"] for r in run_store.landed_tasks(tmp_path, SINCE, UNTIL).rows] == ["at-since"]
    (tmp_path / "cox.db").unlink()
    land_log(tmp_path, ("e-1", "at-since", SINCE), ("e-1", "at-until", UNTIL))
    got = run_store.landed_tasks(tmp_path, SINCE, UNTIL)
    assert (got.source, [r["task_id"] for r in got.rows]) == ("land_records", ["at-since"])


def test_a_failed_land_and_an_absent_store_and_log_read_as_no_lands(tmp_path):
    assert run_store.landed_tasks(tmp_path, SINCE, UNTIL) == run_store.Landed((), "land_records")
    failed = {"ts": INSIDE, "run": "e-1", "task": "a", "steps_reached": ["push"], "exit": 1, "pr": None}
    (tmp_path / "land.jsonl").write_text(json.dumps(failed) + "\nnot json\n")
    assert run_store.landed_tasks(tmp_path, SINCE, UNTIL).rows == ()
