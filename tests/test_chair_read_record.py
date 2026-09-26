from agent_tools.chair_read_record import ACTION_LOG, action_line, recorder


def test_action_line_sorts_keys_and_adds_ts_and_epoch():
    action = {"kind": "needs_chair", "initiative": "x", "cause": "code"}
    assert (
        action_line(action, 3, "t")
        == '{"cause": "code", "epoch": 3, "initiative": "x", "kind": "needs_chair", "ts": "t"}'
    )


def test_recorder_appends_lines_in_order(tmp_path):
    record = recorder(tmp_path, lambda: 1, lambda: "t")
    first = {"kind": "needs_chair", "initiative": "a", "cause": "code"}
    second = {"kind": "needs_chair", "initiative": "b", "cause": "spec"}
    record(first)
    record(second)
    assert (tmp_path / ACTION_LOG).read_text().splitlines() == [
        action_line(first, 1, "t"),
        action_line(second, 1, "t"),
    ]
