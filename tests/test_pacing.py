from datetime import UTC, datetime

from agent_tools.pacing import Policy, Window, assess

_START = datetime(2026, 9, 5, 0, 0, 0, tzinfo=UTC)
_END = datetime(2026, 9, 5, 10, 0, 0, tzinfo=UTC)


def _policy(min_headroom_usd: float = 10.0) -> Policy:
    # 4 thresholds against a 2-step tier ladder and a 1-step effort ladder
    # (max_rung 3): rungs 0-3 walk the ladder down to cheap/low as
    # go_degraded, and only a ratio past the 4th threshold stops.
    return Policy(
        pace_thresholds=(1.2, 1.5, 2.0, 2.5),
        tier_ladder=("deep", "standard", "cheap"),
        effort_ladder=("high", "low"),
        min_headroom_usd=min_headroom_usd,
    )


def _window(spent_usd: float, ceiling_usd: float | None, burn_usd_per_hour: float = 0.0) -> Window:
    return Window(
        start=_START,
        end=_END,
        spent_usd=spent_usd,
        ceiling_usd=ceiling_usd,
        burn_usd_per_hour=burn_usd_per_hour,
        runs_in_flight=1,
    )


def test_go_exactly_at_the_first_pace_threshold_is_not_yet_degraded():
    now = _START.replace(hour=5)  # elapsed_fraction 0.5, ratio exactly 1.2
    result = assess(_window(spent_usd=60, ceiling_usd=100), _policy(), now)
    assert result.spent_fraction == 0.6
    assert result.elapsed_fraction == 0.5
    assert result.verdict == "go"
    assert result.tier_ceiling == "deep"
    assert result.effort_ceiling == "high"
    assert result.hold_until is None
    assert "\n" not in result.reason


def test_go_when_headroom_exactly_equals_the_minimum():
    now = _START.replace(hour=5)  # same ratio 1.2, headroom pinned at the min itself
    result = assess(_window(spent_usd=60, ceiling_usd=100, burn_usd_per_hour=6.0), _policy(), now)
    assert result.headroom_usd == 10.0
    assert result.verdict == "go"
    assert "\n" not in result.reason


def test_hold_when_headroom_drops_just_below_the_minimum():
    now = _START.replace(hour=5)  # same on-pace ratio 1.2; only headroom crosses the line
    result = assess(_window(spent_usd=60, ceiling_usd=100, burn_usd_per_hour=6.2), _policy(), now)
    assert result.headroom_usd == 9.0
    assert result.verdict == "hold"
    assert result.hold_until == _END
    assert result.tier_ceiling == "cheap"
    assert result.effort_ceiling == "low"
    assert "\n" not in result.reason


def test_go_degraded_first_rung_boundary_tightens_tier_only():
    now = _START.replace(hour=5)  # elapsed_fraction 0.5, ratio exactly 1.5
    result = assess(_window(spent_usd=75, ceiling_usd=100), _policy(), now)
    assert result.verdict == "go_degraded"
    assert result.tier_ceiling == "standard"
    assert result.effort_ceiling == "high"


def test_go_degraded_second_rung_boundary_exhausts_tier_before_effort():
    now = _START.replace(hour=4)  # elapsed_fraction 0.4, ratio exactly 2.0, headroom well clear
    result = assess(_window(spent_usd=80, ceiling_usd=100), _policy(), now)
    assert result.headroom_usd == 20.0
    assert result.verdict == "go_degraded"
    assert result.tier_ceiling == "cheap"
    assert result.effort_ceiling == "high"


def test_go_degraded_reaches_the_fully_degraded_rung_cheap_low_without_stopping():
    now = _START.replace(hour=4)  # elapsed_fraction 0.4, ratio 2.375: past 3 of 4 thresholds
    result = assess(_window(spent_usd=190, ceiling_usd=200), _policy(), now)
    assert result.headroom_usd == 10.0  # not below the minimum
    assert result.verdict == "go_degraded"
    assert result.tier_ceiling == "cheap"
    assert result.effort_ceiling == "low"


def test_stop_fires_from_pace_alone_even_with_a_burn_rate_that_would_trip_hold():
    now = _START.replace(hour=4)  # elapsed_fraction 0.4, ratio 2.625: past every threshold
    result = assess(_window(spent_usd=210, ceiling_usd=200, burn_usd_per_hour=1.0), _policy(), now)
    assert result.headroom_usd == -16.0  # deep in hold territory too, yet stop still wins
    assert result.verdict == "stop"
    assert result.tier_ceiling == "cheap"
    assert result.effort_ceiling == "low"
    assert result.hold_until is None


def test_ratio_defaults_to_zero_at_the_very_start_of_the_window():
    result = assess(_window(spent_usd=0, ceiling_usd=100), _policy(), _START)
    assert result.elapsed_fraction == 0.0
    assert result.verdict == "go"
    assert result.tier_ceiling == "deep"


def test_ratio_is_infinite_when_time_has_not_elapsed_but_money_has_been_spent():
    result = assess(_window(spent_usd=5, ceiling_usd=100), _policy(), _START)
    assert result.elapsed_fraction == 0.0
    assert result.spent_fraction == 0.05
    assert result.verdict == "stop"
    assert result.tier_ceiling == "cheap"
    assert result.effort_ceiling == "low"


def test_elapsed_fraction_is_complete_for_a_zero_length_window():
    window = Window(start=_START, end=_START, spent_usd=50, ceiling_usd=100,
                     burn_usd_per_hour=0.0, runs_in_flight=1)
    result = assess(window, _policy(), _START)
    assert result.elapsed_fraction == 1.0
    assert result.verdict == "go"


def test_a_non_positive_ceiling_is_unmeasured_not_a_false_zero_spend():
    now = _START.replace(hour=5)
    result = assess(_window(spent_usd=50, ceiling_usd=0.0), _policy(), now)
    assert result.spent_fraction is None
    assert result.headroom_usd is None
    assert result.verdict == "go"
    assert "unmeasured" in result.reason


def test_unmeasured_window_reports_go_without_guessing_a_ceiling():
    now = _START.replace(hour=5)
    result = assess(_window(spent_usd=50, ceiling_usd=None), _policy(), now)
    assert result.spent_fraction is None
    assert result.headroom_usd is None
    assert result.verdict == "go"
    assert result.hold_until is None
    assert "unmeasured" in result.reason
