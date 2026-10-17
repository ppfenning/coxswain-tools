from agent_tools.chair_facts import empty_decompose_facts


def test_a_decomposed_intake_with_zero_items_and_an_ended_run_appears():
    facts = empty_decompose_facts(decomposed={"i": "i-decompose-1"}, live=set(), item_counts={"i": 0})
    assert facts == [{"initiative": "i", "run": "i-decompose-1"}]


def test_a_live_decompose_run_is_not_reported_even_with_zero_items():
    facts = empty_decompose_facts(decomposed={"i": "i-decompose-1"}, live={"i"}, item_counts={"i": 0})
    assert facts == []


def test_only_the_ended_empty_initiative_is_reported_among_several():
    facts = empty_decompose_facts(
        decomposed={"i": "i-2", "full": "full-1", "busy": "busy-1"},
        live={"busy"},
        item_counts={"i": 0, "full": 3, "busy": 0},
    )
    assert facts == [{"initiative": "i", "run": "i-2"}]


def test_one_or_more_stored_items_is_not_reported():
    facts = empty_decompose_facts(decomposed={"i": "i-decompose-1"}, live=set(), item_counts={"i": 2})
    assert facts == []
