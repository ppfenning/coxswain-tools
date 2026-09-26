from agent_tools.chair_read_stranded import keep_stranded_keys


def test_a_full_row_keeps_exactly_the_five_keys():
    row = {"run": "r1", "task": "t1", "phase": "p1", "branch": "b1", "remedy": "cox runs land r1 --task t1 --repo /x"}
    assert keep_stranded_keys([row]) == [row]


def test_extra_keys_on_the_input_row_are_dropped():
    row = {"run": "r1", "task": "t1", "phase": "p1", "branch": "b1", "remedy": None, "review": {"verdict": "approve"}, "repo": "/x"}
    assert keep_stranded_keys([row]) == [{"run": "r1", "task": "t1", "phase": "p1", "branch": "b1", "remedy": None}]


def test_an_empty_input_gives_an_empty_list():
    assert keep_stranded_keys([]) == []
