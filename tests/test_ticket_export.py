import pytest

from agent_tools.queue_rows import coerce_priority, parse_item, render_item

ROW = {
    "kind": "task",
    "initiative": "queue",
    "task_id": "one",
    "phase": "p1",
    "state": "ready",
    "needs": [],
    "title": "One",
    "surfaces": [],
    "body": "Body.\n",
    "extra": {},
}


def _round_trip(priority: object) -> tuple[str, object]:
    text = render_item({**ROW, "extra": {"priority": priority}})
    return text, parse_item("task", ("queue", "p1", "one.md"), text)["extra"]["priority"]


@pytest.mark.parametrize("priority", [9, "9"])
def test_numeric_priority_round_trips_as_int(priority: object) -> None:
    text, back = _round_trip(priority)
    assert "\npriority: 9\n" in text
    assert back == 9 and isinstance(back, int)


def test_non_numeric_priority_is_left_as_it_was() -> None:
    text, back = _round_trip("urgent")
    assert "\npriority: urgent\n" in text
    assert back == "urgent"


@pytest.mark.parametrize("value", [None, 1.5, True, "9.5", "", "high"])
def test_coerce_priority_leaves_what_is_not_an_int_string(value: object) -> None:
    assert coerce_priority(value) is value
