from pathlib import Path

from agent_tools import chair_read_phase_records, run_store
from agent_tools.chair_read_phase_records import phase_keys, read_phase_records, recorded_keys


def _approved(task: str, run: str, phase: str = "p1") -> dict:
    return {"id": task, "initiative": "i", "repo": "/home/user/app", "phase": phase, "phase_done": True, "needs": [], "run": run, "needs_fetch": False}


def test_phase_keys_are_distinct_sorted_and_skip_an_unknown_run():
    approved = [_approved("b", "i-2"), _approved("a", "i-1"), _approved("c", "i-1"), _approved("d", "")]
    assert phase_keys(approved) == ["i-1:p1", "i-2:p1"]


def test_a_key_is_recorded_by_its_runs_manifest_or_by_its_file():
    manifests = {"i-1": [{"run_id": "i-1:p1"}, {"run_id": "i-1:p2"}], "i-3": [{"run_id": "i-3:p0"}]}
    keys = ["i-1:p1", "i-2:p1", "i-3:p1", "i-4:p1"]
    assert recorded_keys(keys, manifests, lambda key: key == "i-2:p1") == ["i-1:p1", "i-2:p1"]


def test_read_phase_records_reads_the_manifests_once_and_checks_the_record_file(tmp_path: Path, monkeypatch):
    reads: list = []
    monkeypatch.setattr(run_store, "all_phase_manifests", lambda d: reads.append(d) or {"i-1": [{"run_id": "i-1:p1"}]})
    (tmp_path / "i-2:p1.json").write_text("{}")
    approved = [_approved("a", "i-1"), _approved("b", "i-2"), _approved("c", "i-3")]
    assert read_phase_records(tmp_path, approved) == ["i-1:p1", "i-2:p1"]
    assert reads == [tmp_path]


def test_read_phase_records_reads_nothing_without_an_approved_run(monkeypatch):
    monkeypatch.setattr(run_store, "all_phase_manifests", lambda d: (_ for _ in ()).throw(AssertionError("read")))
    assert chair_read_phase_records.read_phase_records(Path("/home/user/ws/runs"), [_approved("a", "")]) == []
