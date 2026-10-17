from datetime import UTC, datetime, timedelta

from agent_tools.chair_land_freeze import LandFreeze, status_text
from agent_tools.chair_report import format_status

NOW = datetime(2026, 10, 17, 18, 5, tzinfo=UTC)  # 14:05 EDT
_NEEDS = [{"kind": "needs_chair", "initiative": "epic-a", "cause": "harness"}]
_LANDED = [{"action": {"kind": "land"}, "status": "landed", "reason": ""}]
_BASELINE = (
    "chair 10-17 14:05 EDT | lanes 2/4 | lands 1 | limits 5h 42% (meter) weekly 61%/90% (meter) | holding"
    " | needs chair: epic-a:harness | housekeeping never"
)
_FROZEN_TEXT = "lands frozen until 19:35 (release cut)"


def _facts(**extra):
    return {
        "limits": {"hard_stop": False, "weekly_fraction": 0.61, "hard_stop_fraction": 0.9,
                   "launch_cap": 2, "go_degraded": False, "five_hour_fraction": 0.42,
                   "window_start_day": None, "window_source": "meter", "weekly_source": "meter"},
        "dispatch": {"max_in_flight": 4, "live_runs": 2},
        "last_housekeeping_at": None,
        **extra,
    }


def _line(**extra):
    return format_status(_facts(**extra), _NEEDS, _LANDED, NOW)


def test_a_live_freeze_adds_the_shared_text_after_limits():
    freeze = LandFreeze(reason="release cut", expires_at=NOW + timedelta(minutes=90))
    line = _line(land_freeze=freeze)
    assert _FROZEN_TEXT in line.split(" | ")
    assert status_text(freeze) == _FROZEN_TEXT
    assert line == _BASELINE.replace(" | holding", f" | {_FROZEN_TEXT} | holding")


def test_no_freeze_leaves_the_line_as_it_was():
    assert _line() == _BASELINE


def test_a_none_freeze_leaves_the_line_as_it_was():
    assert _line(land_freeze=None) == _BASELINE


def test_a_freeze_expiring_now_leaves_the_line_as_it_was():
    assert _line(land_freeze=LandFreeze(reason="release cut", expires_at=NOW)) == _BASELINE


def test_an_expired_freeze_leaves_the_line_as_it_was():
    expired = LandFreeze(reason="release cut", expires_at=NOW - timedelta(minutes=1))
    assert _line(land_freeze=expired) == _BASELINE
