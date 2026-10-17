from agent_tools.notify_core import Event, render
from agent_tools.notify_events import events_from_tick

STRANDED = {"kind": "needs_chair", "initiative": "init-a", "cause": "stranded", "reason": "no patch"}
UNRELATED = {"kind": "needs_chair", "initiative": "init-b", "cause": "odd"}
LOGIN = {"kind": "needs_chair", "host": "lane1", "cause": "login_lapsed"}
STALLED = {
    "kind": "needs_chair",
    "initiative": "init-c",
    "run": "run-9",
    "cause": "stalled",
    "reason": "build t1, idle 45m",
}
IDLE = {
    "kind": "needs_chair",
    "initiative": "init-d",
    "cause": "idle_stall",
    "reason": "nothing moved",
    "signature": "sig1",
}


def test_stranded_gives_one_needs_chair_event():
    events = events_from_tick({}, [STRANDED])
    assert [(e.kind, e.key) for e in events] == [("needs_chair", "needs_chair:init-a:stranded")]
    assert "init-a" in events[0].title and "stranded" in events[0].body and "no patch" in events[0].body


def test_ticket_cause_gives_one_event_with_cause_and_reason():
    action = {"kind": "needs_chair", "initiative": "init-t", "cause": "ticket", "reason": "ticket body is vague"}
    (event,) = events_from_tick({}, [action])
    assert event.key == "needs_chair:init-t:ticket"
    assert "ticket" in event.body and "ticket body is vague" in event.body


def test_review_cause_gives_one_event():
    action = {"kind": "needs_chair", "initiative": "init-r", "cause": "review", "reason": "revise"}
    assert [e.key for e in events_from_tick({}, [action])] == ["needs_chair:init-r:review"]


def test_self_clearing_causes_give_no_event():
    actions = [{"kind": "needs_chair", "initiative": f"init-{c}", "cause": c, "reason": "r"}
               for c in ("no_run", "steer_deferred", "held_file")]
    real = {"kind": "needs_chair", "initiative": "init-t", "cause": "ticket", "reason": "r"}
    assert [e.key for e in events_from_tick({}, [*actions, real])] == ["needs_chair:init-t:ticket"]


def test_a_remedy_command_is_carried_in_the_body():
    action = {**UNRELATED, "reason": "stuck", "remedy": "carry", "command": ["cox", "runs", "land", "r1"]}
    (event,) = events_from_tick({}, [action])
    assert event.body == "init-b needs you: odd.\nRemedy: cox runs land r1\nstuck"


def test_no_remedy_command_leaves_no_remedy_line():
    (absent,) = events_from_tick({}, [{**UNRELATED, "reason": "stuck"}])
    (empty,) = events_from_tick({}, [{**UNRELATED, "reason": "stuck", "command": []}])
    assert absent.body == empty.body == "init-b needs you: odd.\nstuck"


def test_a_long_reason_cannot_push_the_remedy_out_of_the_pushed_body():
    action = {**UNRELATED, "reason": "x" * 600, "command": ["cox", "runs", "land", "r1"]}
    (event,) = events_from_tick({}, [action])
    assert "Remedy: cox runs land r1" in render(event)[1]


def test_a_harness_cause_with_no_failures_still_gives_one_event():
    action = {"kind": "needs_chair", "initiative": "init-h", "cause": "harness", "harness_failures": 0}
    assert [e.key for e in events_from_tick({}, [action])] == ["needs_chair:init-h:harness"]


def test_same_initiative_and_cause_share_one_key():
    first = {**UNRELATED, "reason": "one"}
    second = {**UNRELATED, "reason": "two"}
    assert [e.key for e in events_from_tick({}, [first, second])] == ["needs_chair:init-b:odd"]


def test_a_later_action_with_a_remedy_beats_an_earlier_one_without():
    bare = {**UNRELATED, "reason": "one"}
    fixed = {**UNRELATED, "reason": "two", "command": ["cox", "go"]}
    (event,) = events_from_tick({}, [bare, fixed])
    assert event.body == "init-b needs you: odd.\nRemedy: cox go\ntwo"


def test_an_action_with_no_initiative_is_named_by_its_task_and_keyed_apart_from_another():
    one = {"kind": "needs_chair", "initiative": "", "task_id": "t1", "cause": "conflict"}
    two = {"kind": "needs_chair", "initiative": "", "task_id": "t2", "cause": "conflict"}
    events = events_from_tick({}, [one, two])
    assert [e.key for e in events] == ["needs_chair:t1:conflict", "needs_chair:t2:conflict"]
    assert events[0].title == "Needs you: t1"


def test_login_with_url_clicks_through():
    events = events_from_tick({}, [{**LOGIN, "url": "https://login.example/x"}])
    assert events == [
        Event("host_login", "host_login:lane1", "Login lapsed on lane1", events[0].body, "https://login.example/x")
    ]


def test_login_without_url_names_the_login():
    (event,) = events_from_tick({}, [LOGIN])
    assert event.click == "" and "claude login" in event.body and "lane1" in event.body


def test_stalled_gives_stall_by_run():
    (event,) = events_from_tick({}, [STALLED])
    assert (event.kind, event.key) == ("stall", "stall:run-9")
    assert "run-9" in event.body and "init-c" in event.body and "idle 45m" in event.body


def test_idle_stall_keys_on_signature_then_initiative():
    (signed,) = events_from_tick({}, [IDLE])
    assert (signed.kind, signed.key, signed.body) == ("stall", "stall:idle:sig1", "nothing moved")
    (bare,) = events_from_tick({}, [{k: v for k, v in IDLE.items() if k != "signature"}])
    assert bare.key == "stall:idle:init-d"


def test_login_stalled_and_idle_never_give_needs_chair():
    facts = {"quarantines": [{"initiative": "init-c", "harness_failures": 9}, {"initiative": "init-d", "harness_failures": 9}]}
    events = events_from_tick(facts, [LOGIN, STALLED, IDLE])
    assert [e.kind for e in events] == ["host_login", "stall", "stall"]


def test_a_dependency_wait_gives_no_event_beside_a_ticket_cause():
    wait = {"kind": "needs_chair", "initiative": "init-w", "cause": "waiting on x"}
    ticket = {"kind": "needs_chair", "initiative": "init-t", "cause": "ticket", "reason": "ticket body is vague"}
    assert [e.key for e in events_from_tick({}, [wait, ticket])] == ["needs_chair:init-t:ticket"]


def test_healthy_tick_gives_nothing():
    assert events_from_tick({}, [{"kind": "mark_lost", "initiative": "init-a"}, {"kind": "noop"}]) == []
    assert events_from_tick({}, []) == []


def test_action_order_kept_and_repeated_key_once():
    events = events_from_tick({}, [STALLED, LOGIN, STRANDED, LOGIN, STRANDED])
    assert [e.key for e in events] == ["stall:run-9", "host_login:lane1", "needs_chair:init-a:stranded"]
