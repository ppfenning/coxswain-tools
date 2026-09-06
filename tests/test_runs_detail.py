from types import SimpleNamespace

from agent_tools.events import Event, from_trace_names
from agent_tools.runs_detail import Detail, NodeCall, detail, render


def test_timeline_orders_by_canonical_node_order_and_picks_up_verdicts():
    # from_trace_names gives every node of one attempt the same seq and the
    # edge lists trace files alphabetically -- neither carries start order.
    events = from_trace_names("r1", ["build-1.jsonl", "plan-1.jsonl", "review_adversary-1.jsonl"])
    assert {e.seq for e in events} == {1000}
    events = events + [Event("r1", "verdict", 1, {"node": "plan", "verdict": "approve"})]
    calls = [
        {"node": "build", "attempt": 1, "turns": 4, "cost_usd": 0.5},
        {"node": "plan", "attempt": 1, "turns": 2, "cost_usd": 0.1},
        {"node": "review_adversary", "attempt": 1, "turns": 1, "cost_usd": 0.2},
    ]
    d = detail("r1", True, events, calls, {}, [])
    assert [nc.node for nc in d.timeline] == ["plan", "build", "review_adversary"]
    assert d.timeline[0].verdict == "approve"
    assert d.timeline[1].verdict == ""


def test_objection_prefers_the_arbitration_over_the_adversary():
    events = [Event("r1", "verdict", 1000, {"node": "arbitrate", "verdict": "revise"})]
    record = {
        "arbitration": SimpleNamespace(verdict="revise", reasoning="Arbitration says X. More detail."),
        "adversary": [{"why_wrong": "Adversary says Y."}],
    }
    d = detail("r1", True, events, [], record, [])
    assert d.objection == "Arbitration says X"


def test_objection_falls_back_to_the_adversarys_first_why_wrong():
    events = [Event("r1", "verdict", 1000, {"node": "arbitrate", "verdict": "revise"})]
    record = {"adversary": [{"why_wrong": "Adversary says Y. More."}]}
    d = detail("r1", True, events, [], record, [])
    assert d.objection == "Adversary says Y"


def test_no_objection_when_the_last_verdict_is_not_revise():
    events = [Event("r1", "verdict", 1000, {"node": "arbitrate", "verdict": "approve"})]
    record = {"arbitration": SimpleNamespace(verdict="approve", reasoning="Fine. Good.")}
    d = detail("r1", True, events, [], record, [])
    assert d.objection == ""


def test_a_quarantined_run_carries_its_reason():
    events = [Event("r1", "task_quarantined", 5, {"task": "t1", "reason": "boom"})]
    d = detail("r1", False, events, [], {}, [])
    assert d.status == "quarantined"
    assert d.quarantine_reason == "boom"


def test_an_empty_record_yields_empty_strings_not_a_raise():
    d = detail("r1", True, [], [], {}, [])
    assert d.objection == ""
    assert d.quarantine_reason == ""
    assert d.files_touched == ()
    assert d.changed_lines == 0
    assert d.last_calls == ()


def test_last_calls_is_the_tails_last_three_entries():
    d = detail("r1", True, [], [], {}, ["Read", "Grep", "Edit", "Bash"])
    assert d.last_calls == ("Grep", "Edit", "Bash")


def test_files_touched_and_changed_lines_come_from_change_facts():
    record = {"change_facts": {"files_touched": ["a.py", "b.py"], "changed_lines": 12}}
    d = detail("r1", True, [], [], record, [])
    assert d.files_touched == ("a.py", "b.py")
    assert d.changed_lines == 12


def test_render_cuts_every_line_to_width():
    d = Detail(
        run="r1",
        alive=True,
        status="running",
        timeline=(NodeCall(node="plan", attempt=1, turns=3, cost_usd=1.2345, verdict="approve"),),
        objection="a very long objection sentence that should be cut",
        quarantine_reason="",
        files_touched=("a.py", "b.py"),
        changed_lines=10,
        last_calls=("Read", "Edit"),
    )
    lines = render(d, 10)
    assert lines
    assert all(len(line) <= 10 for line in lines)
