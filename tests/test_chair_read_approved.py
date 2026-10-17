import json
from pathlib import Path

from agent_tools.chair_read_approved import (
    approved_rows,
    phase_task_rows,
    read_approved,
    read_fetch_facts,
    read_unsettled_runs,
    with_runs,
)


def _item(id: str, state: str, phase: str = "build", needs: list[str] | None = None) -> dict:
    return {"id": id, "initiative": "init", "phase": phase, "state": state, "repo": "r", "needs": needs or []}


def test_an_approved_item_yields_one_row_with_the_five_keys() -> None:
    assert approved_rows([_item("a", "approved")]) == [
        {"id": "a", "initiative": "init", "repo": "r", "phase": "build", "phase_done": True, "needs": [], "run": "", "needs_fetch": False}
    ]


def test_a_quarantined_only_phase_yields_a_phase_tasks_row() -> None:
    items = [_item("a", "quarantined", "p1"), _item("b", "ready", "p1"), _item("c", "ready", "p2")]
    assert phase_task_rows(items) == [{"initiative": "init", "phase": "p1", "tasks": [("a", "quarantined"), ("b", "ready")]}]


def test_a_draft_item_is_skipped() -> None:
    assert approved_rows([_item("a", "todo"), _item("b", "ready")]) == []


def test_phase_done_is_false_while_a_sibling_is_not_done() -> None:
    rows = approved_rows([_item("a", "approved"), _item("b", "ready")])
    assert [r["phase_done"] for r in rows] == [False]


def test_phase_done_is_true_once_all_siblings_are_done() -> None:
    rows = approved_rows([_item("a", "approved"), _item("b", "done"), _item("c", "ready", phase="next")])
    assert [r["phase_done"] for r in rows] == [True]


def test_two_approved_siblings_are_each_phase_done() -> None:
    rows = approved_rows([_item("a", "approved"), _item("b", "approved")])
    assert [r["phase_done"] for r in rows] == [True, True]


def test_a_dropped_sibling_does_not_hold_the_phase_open() -> None:
    rows = approved_rows([_item("a", "approved"), _item("b", "dropped")])
    assert [r["phase_done"] for r in rows] == [True]


def test_needs_is_carried_through_as_a_list() -> None:
    needs = ["x", "y"]
    rows = approved_rows([_item("a", "approved", needs=needs)])
    assert rows[0]["needs"] == ["x", "y"]
    assert rows[0]["needs"] is not needs


def test_the_edge_reads_the_work_store(tmp_path: Path) -> None:
    init = tmp_path / "work" / "init"
    (init / "build").mkdir(parents=True)
    (init / "initiative.md").write_text("---\nid: init\nrepo: /r/x\n---\nbody\n", encoding="utf-8")
    (init / "build" / "a.md").write_text("---\nid: a\nstate: approved\nneeds: [z]\n---\nbody\n", encoding="utf-8")
    (init / "build" / "b.md").write_text("---\nid: b\nstate: done\n---\nbody\n", encoding="utf-8")
    assert read_approved(tmp_path) == [
        {"id": "a", "initiative": "init", "repo": "/r/x", "phase": "build", "phase_done": True, "needs": ["z"], "run": "", "needs_fetch": False}
    ]


_TASK = {
    "id": "t", "initiative": "init", "repo": "r", "phase": "build", "phase_done": True, "needs": [], "run": "",
    "needs_fetch": False,
}
_STRANDED = [{"run": "init-1", "task": "t", "phase": "build"}]


def test_a_task_on_an_unfetched_remote_run_needs_a_fetch() -> None:
    assert with_runs([_TASK], _STRANDED, {"init-1": (True, False)})[0]["needs_fetch"] is True


def test_a_task_on_a_fetched_remote_run_needs_none() -> None:
    assert with_runs([_TASK], _STRANDED, {"init-1": (True, True)})[0]["needs_fetch"] is False


def test_a_task_with_no_run_needs_no_fetch() -> None:
    assert with_runs([_TASK], [], {"init-1": (True, False)})[0]["needs_fetch"] is False


def test_the_edge_reads_the_remote_and_fetched_markers(tmp_path: Path) -> None:
    (tmp_path / "init-1.remote.json").write_text("{}", encoding="utf-8")
    assert read_fetch_facts(tmp_path, _STRANDED) == {"init-1": (True, False)}


def test_a_newer_remote_run_is_unsettled_until_it_is_fetched(tmp_path: Path) -> None:
    for run in ("init-1", "init-2", "init-3"):
        (tmp_path / f"{run}.remote.json").write_text("{}", encoding="utf-8")
    (tmp_path / "init-3.fetched.json").write_text("{}", encoding="utf-8")
    records = [{"run": "init-1", "task": "a", "cause": "code"}]
    assert read_unsettled_runs(tmp_path, records) == {"init-2"}


def test_no_marked_record_reads_no_runs(tmp_path: Path) -> None:
    (tmp_path / "init-2.remote.json").write_text("{}", encoding="utf-8")
    assert read_unsettled_runs(tmp_path, [{"run": "init-1", "task": "a", "cause": "harness"}]) == frozenset()


def _work_store(tmp_path: Path) -> None:
    init = tmp_path / "work" / "init"
    (init / "build").mkdir(parents=True)
    (init / "initiative.md").write_text("---\nid: init\nrepo: /r/x\n---\nbody\n", encoding="utf-8")
    (init / "build" / "a.md").write_text("---\nid: a\nstate: approved\n---\nbody\n", encoding="utf-8")


def _store_row(state: str) -> dict:
    return {"initiative": "init", "task_id": "a", "state": state}


def test_a_task_the_store_holds_done_is_not_returned(tmp_path: Path) -> None:
    _work_store(tmp_path)
    assert read_approved(tmp_path, mode="store", store_rows=[_store_row("done")]) == []


def test_a_task_the_store_holds_dropped_is_not_returned(tmp_path: Path) -> None:
    _work_store(tmp_path)
    assert read_approved(tmp_path, mode="store", store_rows=[_store_row("dropped")]) == []


def test_a_store_done_row_wins_in_files_mode(tmp_path: Path) -> None:
    _work_store(tmp_path)
    assert read_approved(tmp_path, mode="files", store_rows=[_store_row("done")]) == []


def _landed_record(tmp_path: Path, landed: bool) -> None:
    record = tmp_path / "runs" / "init-1" / "tasks" / "build"
    record.mkdir(parents=True)
    (record / "a.json").write_text(json.dumps({"landed": landed}), encoding="utf-8")


def test_a_task_landed_after_the_store_snapshot_is_not_returned(tmp_path: Path) -> None:
    _work_store(tmp_path)
    _landed_record(tmp_path, True)
    assert read_approved(tmp_path, mode="store", store_rows=[_store_row("approved")]) == []


def test_a_task_whose_record_is_not_landed_is_still_returned(tmp_path: Path) -> None:
    _work_store(tmp_path)
    _landed_record(tmp_path, False)
    assert [r["id"] for r in read_approved(tmp_path, mode="store", store_rows=[_store_row("approved")])] == ["a"]


def test_a_task_approved_in_file_and_store_is_still_returned(tmp_path: Path) -> None:
    _work_store(tmp_path)
    assert read_approved(tmp_path, mode="store", store_rows=[_store_row("approved")]) == [
        {"id": "a", "initiative": "init", "repo": "/r/x", "phase": "build", "phase_done": True, "needs": [], "run": "", "needs_fetch": False}
    ]
