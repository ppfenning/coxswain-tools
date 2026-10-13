from datetime import UTC, datetime, timedelta

from agent_tools.pacing import Policy, Window, weekly_pace

START = datetime(2026, 10, 5, tzinfo=UTC)
POLICY = Policy(
    pace_thresholds=(1.0, 1.2),
    tier_ladder=("opus", "sonnet"),
    effort_ladder=("high", "low"),
    min_headroom_usd=0.0,
)


def week(spent: float, ceiling: float | None = 1000.0) -> Window:
    return Window(start=START, end=START + timedelta(days=7), spent_usd=spent, ceiling_usd=ceiling,
                  burn_usd_per_hour=0.0, runs_in_flight=0)


MID = START + timedelta(days=3.5)


def test_on_pace_is_full() -> None:
    result = weekly_pace(week(450.0), POLICY, MID)
    assert (result.lane_fraction, result.verdict) == (1.0, "full")
    assert result.target_fraction == 0.45


def test_slightly_over_target_is_reduced() -> None:
    assert weekly_pace(week(500.0), POLICY, MID).lane_fraction == 0.5


def test_1_2x_target_is_reduced() -> None:
    result = weekly_pace(week(540.0), POLICY, MID)
    assert (result.lane_fraction, result.verdict) == (0.25, "reduced")


def test_1_4x_target_is_hold() -> None:
    result = weekly_pace(week(630.0), POLICY, MID)
    assert (result.lane_fraction, result.verdict) == (0.0, "hold")


def test_early_week_is_not_judged() -> None:
    now = START + timedelta(days=7 * 0.02)
    result = weekly_pace(week(900.0), POLICY, now)
    assert (result.lane_fraction, result.verdict) == (1.0, "not_judged")


def test_no_weekly_window_is_not_judged() -> None:
    result = weekly_pace(None, POLICY, MID)
    assert (result.lane_fraction, result.verdict) == (1.0, "not_judged")


def test_no_ceiling_is_not_judged() -> None:
    assert weekly_pace(week(900.0, ceiling=None), POLICY, MID).verdict == "not_judged"


def test_custom_ladder_and_target() -> None:
    policy = Policy(
        pace_thresholds=(1.0, 1.2), tier_ladder=("opus", "sonnet"), effort_ladder=("high", "low"),
        min_headroom_usd=0.0, weekly_target_fraction=0.5, weekly_pace_lanes=((1.0, 0.75), (2.0, 0.1)),
    )
    # target 0.25 at mid-week; 20% spent is ratio 0.8
    assert weekly_pace(week(200.0), policy, MID).lane_fraction == 0.75
    # 40% spent is ratio 1.6
    assert weekly_pace(week(400.0), policy, MID).lane_fraction == 0.1
    # 50% spent is ratio 2.0, at the last bound
    result = weekly_pace(week(500.0), policy, MID)
    assert (result.lane_fraction, result.verdict) == (0.0, "hold")
