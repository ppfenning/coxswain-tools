from agent_tools.router import select_tier


def test_below_min_n_returns_the_default_tier_with_insufficient_n_reason():
    stats_window = {"n": 5, "landed_rate": 0.10, "start": "2026-09-01", "end": "2026-09-14"}
    policy = {"default_tier": "standard", "min_n": 20, "deviation_floor": 0.70, "challenger_n": 10}
    assert select_tier("draft", stats_window, policy) == ("standard", "insufficient_n")


def test_at_or_above_min_n_with_low_landed_rate_deviates_one_tier_up():
    stats_window = {"n": 21, "landed_rate": 0.50, "start": "2026-09-01", "end": "2026-09-14"}
    policy = {"default_tier": "standard", "min_n": 20, "deviation_floor": 0.70, "challenger_n": 10}
    assert select_tier("draft", stats_window, policy) == ("deep", "landed_rate_low")


def test_build_and_arbitrate_never_return_challenger_even_on_schedule():
    stats_window = {"n": 10, "landed_rate": 0.90, "start": "2026-09-01", "end": "2026-09-14"}
    policy = {"default_tier": "standard", "min_n": 20, "deviation_floor": 0.70, "challenger_n": 10}
    assert select_tier("build", stats_window, policy) == ("standard", "insufficient_n")
    assert select_tier("arbitrate", stats_window, policy) == ("standard", "insufficient_n")


def test_low_risk_roles_nth_call_returns_the_challenger_tier_and_fixed_reason():
    stats_window = {"n": 10, "landed_rate": 0.90, "start": "2026-09-01", "end": "2026-09-14"}
    policy = {"default_tier": "standard", "min_n": 20, "deviation_floor": 0.70, "challenger_n": 10}
    assert select_tier("draft", stats_window, policy) == ("cheap", "challenger")
