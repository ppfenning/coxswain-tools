from datetime import UTC, datetime

import pytest

from agent_tools.chair_report import format_status, needs_chair_items, waiting_item
from agent_tools.chair_runnable import Blocking, WaitingInitiative

NOW = datetime(2026, 9, 26, 18, 5, tzinfo=UTC)  # 14:05 EDT
FACTS = {
    "limits": {"hard_stop": False, "weekly_fraction": 0.61, "hard_stop_fraction": 0.9, "five_hour_fraction": 0.42,
               "window_start_day": None, "window_source": "meter", "weekly_source": "meter"},
    "dispatch": {"max_in_flight": 4, "live_runs": 2},
    "last_housekeeping_at": None,
}
NEEDS = [{"kind": "needs_chair", "initiative": "epic-a", "cause": "harness"}]
OLD_LINE = (
    "chair 09-26 14:05 EDT | lanes 2/4 | lands 0 | limits 5h 42% (meter) weekly 61%/90% (meter) | holding"
    " | needs chair: epic-a:harness | housekeeping never"
)


@pytest.mark.parametrize(
    ("quarantined", "awaiting_land", "expected"),
    [
        (1, 0, "waiting: init (blocked by p1: 1 quarantined, 0 awaiting land)"),
        (0, 2, "waiting: init (blocked by p1: 0 quarantined, 2 awaiting land)"),
        (2, 1, "waiting: init (blocked by p1: 2 quarantined, 1 awaiting land)"),
    ],
)
def test_waiting_item_renders_blocking_counts(quarantined: int, awaiting_land: int, expected: str) -> None:
    assert waiting_item(WaitingInitiative("init", Blocking("p1", quarantined, awaiting_land))) == expected


def test_existing_action_keeps_its_text() -> None:
    action = {"kind": "needs_chair", "initiative": "init", "phase": "p1", "cause": "stuck"}
    assert needs_chair_items([action]) == ["needs chair: init/p1:stuck"]


def test_status_line_without_a_waiting_action_is_unchanged() -> None:
    assert format_status(FACTS, NEEDS, [], NOW) == OLD_LINE


def test_status_line_shows_a_waiting_initiative_beside_the_other_actions() -> None:
    waiting = WaitingInitiative("init", Blocking("p1", 2, 1))
    assert format_status(FACTS, [*NEEDS, waiting], [], NOW) == OLD_LINE.replace(
        "| needs chair",
        "| waiting: init (blocked by p1: 2 quarantined, 1 awaiting land) | needs chair",
    )
