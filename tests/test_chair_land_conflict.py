from agent_tools.chair_land_conflict import standing

CONFLICT = {"initiative": "i", "phase": "p", "run": "i-1", "paths": ["a.py"]}


def test_a_conflict_for_the_same_initiative_phase_and_run_is_returned():
    assert standing([CONFLICT], "i", "p", "i-1") == CONFLICT


def test_a_different_run_id_returns_nothing():
    assert standing([CONFLICT], "i", "p", "i-2") is None


def test_an_empty_list_returns_nothing():
    assert standing([], "i", "p", "i-1") is None
