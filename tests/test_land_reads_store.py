import json
from pathlib import Path

from agent_tools import cli, run_store


def test_store_record_wins_over_the_file_record():
    assert cli._choose_record({"a": 1}, {"a": 2}) == ({"a": 1}, "store")


def test_file_record_is_used_when_the_store_has_none():
    assert cli._choose_record(None, {"a": 2}) == ({"a": 2}, "file")


def test_neither_record_returns_none_and_no_label():
    assert cli._choose_record(None, None) == (None, None)


def write_task(runs_dir: Path, body: dict) -> None:
    path = runs_dir / "epic-x-5" / "tasks" / "seams" / "t1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(body), encoding="utf-8")


def test_land_load_reads_the_store_with_the_phase_and_task_from_the_file_name(tmp_path, monkeypatch):
    write_task(tmp_path, {"branch": "from-file"})
    asked = []

    def fake(runs_dir, run_id, phase_id, task_id):
        asked.append((run_id, phase_id, task_id))
        return {"branch": "from-store"}

    monkeypatch.setattr(run_store, "task_record", fake)
    record, where, count, source = cli._land_load(tmp_path, "epic-x-5", "t1")
    assert asked == [("epic-x-5", "seams", "t1")]
    assert record == {"run": "epic-x-5", "task": "t1", "phase": "seams", "branch": "from-store"}
    assert (where.endswith("seams/t1.json"), count, source) == (True, 1, "store")


def test_land_load_falls_back_to_the_file_when_the_store_returns_none(tmp_path, monkeypatch):
    write_task(tmp_path, {"branch": "from-file"})
    monkeypatch.setattr(run_store, "task_record", lambda *a: None)
    record, _, count, source = cli._land_load(tmp_path, "epic-x-5", "t1")
    assert record == {"branch": "from-file", "run": "epic-x-5", "task": "t1", "phase": "seams"}
    assert (count, source) == (1, "file")


def test_land_load_finds_nothing_and_skips_the_store_when_no_file_matches(tmp_path, monkeypatch):
    def boom(*a):
        raise AssertionError("store must not be asked")

    monkeypatch.setattr(run_store, "task_record", boom)
    record, _, count, source = cli._land_load(tmp_path, "epic-x-5", "t1")
    assert (record, count, source) == (None, 0, None)
