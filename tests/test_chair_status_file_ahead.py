from datetime import UTC, datetime

from agent_tools.chair_facts import read_queue_files
from agent_tools.chair_report import file_ahead_items, format_status
from agent_tools.queue_export import text_hash
from agent_tools.queue_rows import render_item, row_path

NOW = datetime(2026, 9, 26, 18, 5, tzinfo=UTC)  # 14:05 EDT
_NEEDS = [{"kind": "needs_chair", "initiative": "epic-a", "cause": "harness"}]
_BASELINE = (
    "chair 09-26 14:05 EDT | lanes 2/4 | lands 1 | limits 5h 42% (meter) weekly 61%/90% (meter) | holding"
    " | needs chair: epic-a:harness | housekeeping never"
)


def _facts(**extra):
    return {
        "limits": {"hard_stop": False, "weekly_fraction": 0.61, "hard_stop_fraction": 0.9, "launch_cap": 2,
                   "go_degraded": False, "five_hour_fraction": 0.42, "window_start_day": None,
                   "window_source": "meter", "weekly_source": "meter"},
        "dispatch": {"max_in_flight": 4, "live_runs": 2},
        "last_housekeeping_at": None,
        **extra,
    }


def _file(task_id, stored_text, disk_text):
    return {"task_id": task_id, "stored_hash": text_hash(stored_text), "file_text": disk_text}


def _line(facts, actions=_NEEDS):
    return format_status(facts, actions, [{"action": {"kind": "land"}, "status": "landed", "reason": ""}], NOW)


def test_a_file_that_differs_from_its_stored_hash_is_reported():
    line = _line(_facts(queue_files=[_file("t1", "old", "edited")]))
    assert line.endswith("needs chair: epic-a:harness, needs chair: t1:file-ahead-of-store | housekeeping never")


def test_a_file_that_matches_its_stored_hash_is_not_reported():
    assert "file-ahead-of-store" not in _line(_facts(queue_files=[_file("t1", "same", "same")]))


def test_no_ahead_files_leave_the_line_as_it_was():
    assert _line(_facts()) == _BASELINE
    assert _line(_facts(queue_files=[])) == _BASELINE


def test_a_row_with_no_stored_hash_or_no_file_is_not_reported():
    files = [
        {"task_id": "t1", "stored_hash": None, "file_text": "x"},
        {"task_id": "t2", "stored_hash": text_hash("x"), "file_text": None},
    ]
    assert file_ahead_items(files) == []


def test_two_ahead_files_give_one_entry_each_with_no_other_needs():
    files = [_file("t1", "a", "b"), _file("t2", "a", "a"), _file("t3", "a", "c")]
    line = _line(_facts(queue_files=files), actions=[])
    assert "needs chair: t1:file-ahead-of-store, needs chair: t3:file-ahead-of-store" in line
    assert "needs chair: none" not in line


def test_read_queue_files_pairs_each_task_row_with_its_stored_hash_and_disk_text(tmp_path):
    row = {"kind": "task", "state": "ready", "initiative": "epic-a", "phase": "p1", "task_id": "t1", "title": "T", "body": "b"}
    other = {**row, "task_id": "t2"}
    (tmp_path / "work" / "epic-a" / "p1").mkdir(parents=True)
    (tmp_path / row_path(row)).write_text("hand edited")
    stored = {("epic-a", "t1"): text_hash(render_item({**row, "needs": [], "surfaces": [], "extra": {}})), ("epic-a", "t2"): "x"}
    files = read_queue_files(tmp_path, [row, other, {"kind": "initiative", "initiative": "epic-a"}], stored)
    assert [(f["task_id"], f["file_text"]) for f in files] == [("t1", "hand edited"), ("t2", None)]
    assert file_ahead_items(files) == ["needs chair: t1:file-ahead-of-store"]
