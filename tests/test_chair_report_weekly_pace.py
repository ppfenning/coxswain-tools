from agent_tools.chair_report import _weekly

BASE = {"weekly_fraction": 0.52, "hard_stop_fraction": 0.93, "weekly_source": "meter"}


def test_a_judged_pace_prints_the_target_and_the_verdict():
    limits = {**BASE, "weekly_target_fraction": 0.45, "weekly_pace": "reduced"}
    assert _weekly(limits) == "weekly 52%/93% (meter) target 45% reduced"
    assert _weekly({**limits, "window_start_day": "mon"}) == "weekly 52%/93% (meter) target 45% reduced since mon"


def test_not_judged_prints_what_limits_without_the_keys_print():
    judged_not = {**BASE, "weekly_target_fraction": 0.45, "weekly_pace": "not_judged", "window_start_day": "mon"}
    assert _weekly(judged_not) == _weekly({**BASE, "window_start_day": "mon"})
    assert _weekly({**BASE, "weekly_target_fraction": None, "weekly_pace": "not_judged"}) == _weekly(BASE)


def test_limits_without_the_keys_print_the_line_as_before():
    assert _weekly(BASE) == "weekly 52%/93% (meter)"
    assert _weekly({**BASE, "window_start_day": "mon"}) == "weekly 52%/93% (meter) since mon"
