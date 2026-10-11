from datetime import UTC, datetime
from pathlib import Path

from agent_tools import route
from agent_tools.chair_read_docket import _ready_age, _work_items, docket_from_builder, docket_from_rows, read_docket

ROOT = Path("/ws")


def _item(
    id: str, initiative: str, updated_at: str | None, state: str = "ready", priority: int = 0, needs: tuple[str, ...] = (),
) -> dict:
    return {
        "id": id, "initiative": initiative, "phase": "p1", "state": state, "needs": list(needs),
        "file": f"p1/{id}.md", "priority": priority, "updated_at": updated_at,
    }


def _order(items: list[dict]) -> list[str]:
    docket = docket_from_builder(route.initiative_summaries(items), items, 0, 4)
    return [i["id"] for i in docket["initiatives"]]


def test_higher_priority_wins_over_an_older_task():
    items = [_item("t", "a", "2026-01-01T00:00:00Z", priority=1), _item("t", "b", "2026-09-01T00:00:00Z", priority=5)]
    assert _order(items) == ["b", "a"]


def test_equal_priority_puts_the_older_task_first_even_for_a_late_id():
    items = [_item("t", "a", "2026-09-01T00:00:00Z"), _item("t", "z", "2026-01-01T00:00:00Z")]
    assert _order(items) == ["z", "a"]


def test_the_oldest_of_an_initiatives_ready_tasks_sets_its_age():
    items = [
        _item("t1", "a", "2026-09-01T00:00:00Z"), _item("t2", "a", "2026-02-01T00:00:00Z"),
        _item("t", "b", "2026-05-01T00:00:00Z"),
    ]
    assert _order(items) == ["a", "b"]


def test_equal_age_falls_back_to_id_ascending():
    items = [_item("t", "z", "2026-01-01T00:00:00Z"), _item("t", "a", "2026-01-01T00:00:00Z")]
    assert _order(items) == ["a", "z"]


def test_an_initiative_with_no_launchable_task_sorts_after_one_with_a_task_then_by_id():
    items = [
        _item("t", "a", "2026-01-01T00:00:00Z", state="todo"),
        _item("t", "b", "2026-09-01T00:00:00Z"),
        _item("t", "c", "2026-08-01T00:00:00Z"),
    ]
    row = {"id": "a", "ready_tasks": []}
    assert _ready_age(row, items) is None
    assert _order(items) == ["c", "b"]


def test_a_task_with_no_age_sorts_after_one_with_an_age_then_by_id():
    items = [_item("t", "a", None), _item("t", "b", "2026-09-01T00:00:00Z"), _item("t", "c", None)]
    assert _order(items) == ["b", "a", "c"]


def test_a_held_task_that_is_older_does_not_set_the_age():
    items = [
        _item("h0", "a", "2026-06-01T00:00:00Z", state="approved"),
        _item("h1", "a", "2026-01-01T00:00:00Z", needs=("h0",)),
        _item("t2", "a", "2026-09-01T00:00:00Z"),
        _item("t", "b", "2026-05-01T00:00:00Z"),
    ]
    row = next(r for r in docket_from_builder(route.initiative_summaries(items), items, 0, 4)["initiatives"] if r["id"] == "a")
    assert [t["id"] for t in row["held_tasks"]] == ["h1"]
    assert _ready_age(row, items) == datetime(2026, 9, 1, tzinfo=UTC)
    assert _order(items) == ["b", "a"]


def test_a_waiting_task_that_is_older_does_not_set_the_age():
    items = [
        _item("w", "a", "2026-01-01T00:00:00Z", needs=("gone",)),
        _item("t", "a", "2026-09-01T00:00:00Z"),
        _item("t", "b", "2026-05-01T00:00:00Z"),
    ]
    assert _order(items) == ["b", "a"]


def _files(*initiatives: str, root: Path = ROOT) -> dict[Path, str]:
    return {root / "work" / name / "p1" / "t.md": "---\nstate: ready\n---\n" for name in initiatives}


def _store_row(initiative: str, updated_at: str) -> dict:
    return {"initiative": initiative, "task_id": "t", "phase": "p1", "state": "ready", "updated_at": updated_at}


_ROWS = [_store_row("a", "2026-09-01T00:00:00Z"), _store_row("z", "2026-01-01T00:00:00Z")]


def test_work_items_carry_updated_at_from_the_store_rows():
    items = _work_items(ROOT, "store", _files("a", "z"), _ROWS)
    assert {i["initiative"]: i["updated_at"] for i in items} == {"a": "2026-09-01T00:00:00Z", "z": "2026-01-01T00:00:00Z"}


def test_read_docket_orders_equal_priority_initiatives_by_the_store_age(tmp_path):
    docket = read_docket(tmp_path, "store", 4, files=_files("a", "z", root=tmp_path), store_rows=_ROWS)
    assert [i["id"] for i in docket["initiatives"]] == ["z", "a"]


NOW = "2026-10-11T00:00:00Z"


def _queue_row(initiative: str, task_id: str = "t", holder: str | None = None, expires_at: str = "", priority: int = 0) -> dict:
    """The `run_store.read_queue` shape: `_QUEUE_COLUMNS` plus the claim keys, with no `updated_at`."""
    return {
        "kind": "task", "initiative": initiative, "task_id": task_id, "phase": "p1", "state": "ready", "needs": [],
        "title": task_id, "surfaces": [], "body": "", "extra": {"priority": priority},
        "holder": holder, "epoch": 0, "expires_at": expires_at,
    }


def _aged(initiative: str, task_id: str, updated_at: str) -> dict:
    return {"initiative": initiative, "id": task_id, "updated_at": updated_at}


def test_docket_from_rows_orders_queue_rows_by_the_work_item_age():
    rows = [_queue_row("a"), _queue_row("z")]
    aged = [_aged("a", "t", "2026-09-01T00:00:00Z"), _aged("z", "t", "2026-01-01T00:00:00Z")]
    assert [i["id"] for i in docket_from_rows(rows, NOW)] == ["a", "z"]
    assert [i["id"] for i in docket_from_rows(rows, NOW, aged)] == ["z", "a"]


def test_a_leased_older_task_does_not_set_the_age():
    rows = [_queue_row("z", "old", holder="run-1", expires_at="2026-10-12T00:00:00Z"), _queue_row("z", "new"), _queue_row("a")]
    aged = [
        _aged("z", "old", "2026-01-01T00:00:00Z"), _aged("z", "new", "2026-09-01T00:00:00Z"),
        _aged("a", "t", "2026-05-01T00:00:00Z"),
    ]
    assert [i["id"] for i in docket_from_rows(rows, NOW, aged)] == ["a", "z"]


def test_a_held_task_does_not_raise_the_priority():
    items = [
        _item("h0", "a", "2026-06-01T00:00:00Z", state="approved"),
        _item("h1", "a", "2026-01-01T00:00:00Z", priority=9, needs=("h0",)),
        _item("t2", "a", "2026-01-01T00:00:00Z"),
        _item("t", "b", "2026-05-01T00:00:00Z", priority=1),
    ]
    assert _order(items) == ["b", "a"]


def test_ages_compare_as_times_not_strings():
    items = [_item("t", "a", "2025-12-31T23:30:00Z"), _item("t", "z", "2026-01-01 00:00:00+02:00")]
    assert _order(items) == ["z", "a"]
