from datetime import UTC, datetime, timedelta

import pytest

from agent_tools import pacing
from agent_tools.chair_facts import limits_facts

START = datetime(2026, 10, 11, 4, 0, tzinfo=UTC)
MID_WEEK = START + timedelta(hours=84)
POLICY = pacing.Policy(
    pace_thresholds=(1.2, 1.5, 2.0),
    tier_ladder=("deep", "standard", "cheap"),
    effort_ladder=("high", "low"),
    min_headroom_usd=1.0,
)


def _weekly(spent: float) -> pacing.Window:
    return pacing.Window(START, START + timedelta(days=7), spent, 100.0, 0.0, 0)


def _assessment(verdict: str = "go") -> pacing.Assessment:
    return pacing.Assessment(0.2, 0.5, 20.0, 10.0, verdict, "deep", "high", None, "on pace")


# The mid-week target is 0.90 * 0.5 = 45 of 100, so spend of 45, 54 and 63 is 1.0, 1.2 and 1.4 times the target.
@pytest.mark.parametrize(
    ("spent", "launch_cap", "lane_fraction", "weekly_pace"),
    [(45.0, 4, 1.0, "full"), (54.0, 1, 0.25, "reduced"), (63.0, 0, 0.0, "hold")],
)
def test_the_launch_cap_follows_the_weekly_pace_at_mid_week(spent, launch_cap, lane_fraction, weekly_pace):
    limits = limits_facts(_assessment(), POLICY, _weekly(spent), 4, now=MID_WEEK)
    assert (limits["launch_cap"], limits["lane_fraction"], limits["weekly_pace"]) == (
        launch_cap, lane_fraction, weekly_pace,
    )
    assert limits["weekly_target_fraction"] == pytest.approx(0.45)


def test_before_five_percent_of_the_week_only_the_hard_stop_applies():
    limits = limits_facts(_assessment(), POLICY, _weekly(30.0), 4, now=START + timedelta(hours=6))
    assert (limits["launch_cap"], limits["weekly_pace"], limits["lane_fraction"]) == (4, "not_judged", 1.0)


def test_a_five_hour_hold_gives_no_launches_whatever_the_weekly_pace():
    limits = limits_facts(_assessment("hold"), POLICY, _weekly(45.0), 4, now=MID_WEEK)
    assert (limits["launch_cap"], limits["weekly_pace"]) == (0, "full")


def test_a_quarter_lane_fraction_of_two_lanes_rounds_up_to_one_not_down_to_zero():
    quarter = pacing.Policy(
        pace_thresholds=(1.2, 1.5, 2.0),
        tier_ladder=("deep",),
        effort_ladder=("high",),
        min_headroom_usd=1.0,
        weekly_pace_lanes=((10.0, 0.25), (20.0, 0.25)),
    )
    limits = limits_facts(_assessment(), quarter, _weekly(45.0), 2, now=MID_WEEK)
    assert (limits["lane_fraction"], limits["launch_cap"]) == (0.25, 1)


def test_without_now_the_weekly_pace_is_not_judged_and_the_cap_is_the_whole_budget():
    limits = limits_facts(_assessment(), POLICY, _weekly(63.0), 4)
    assert (limits["launch_cap"], limits["lane_fraction"], limits["weekly_target_fraction"], limits["weekly_pace"]) == (
        4, 1.0, None, "not_judged",
    )
