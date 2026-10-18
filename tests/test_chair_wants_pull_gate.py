from agent_tools.chair_plan_fill import _wants_pull
from agent_tools.chair_types import Facts


def _pullable(**over) -> Facts:
    """Facts that make every condition of _wants_pull other than the pull age true."""
    base = {
        "initiatives": [],
        "intake": [],
        "work_store_ready": False,
        "sources_configured": True,
    }
    return {**base, **over}


def test_without_a_pull_age_fact_a_pull_is_wanted():
    assert _wants_pull(_pullable(), 1) is True


def test_a_pull_younger_than_ten_minutes_is_not_repeated():
    assert _wants_pull(_pullable(pull_age_s=599), 1) is False


def test_a_pull_of_exactly_ten_minutes_is_repeated():
    assert _wants_pull(_pullable(pull_age_s=600), 1) is True


def test_a_none_pull_age_wants_a_pull():
    assert _wants_pull(_pullable(pull_age_s=None), 1) is True
