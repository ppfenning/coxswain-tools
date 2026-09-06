import json

from agent_tools import records


def test_a_run_launched_while_a_session_holds_the_lock_carries_that_labels():
    files = {"r1.launched.json": json.dumps({"launched_by": "leader-a", "at": "2026-09-06T00:00:00+00:00"})}
    assert records.launched_by_for("r1", files) == "leader-a"


def test_a_run_launched_with_no_held_lock_carries_no_label():
    files = {"r1.launched.json": json.dumps({"at": "2026-09-06T00:00:00+00:00"})}
    assert records.launched_by_for("r1", files) is None


def test_a_run_with_no_launched_file_carries_no_label():
    assert records.launched_by_for("r1", {"r2.launched.json": "{}"}) is None
