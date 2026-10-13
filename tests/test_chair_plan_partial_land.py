from agent_tools.chair_plan_land import plan_lands
from agent_tools.chair_plan_partial import classify_phase, partial_land_action
from agent_tools.chair_read_approved import land_records, mark_landed, phase_task_rows, with_runs
from agent_tools.runs_stranded import stranded


def _facts(tasks: list[tuple[str, str]], approved: list[dict] | None = None) -> dict:
    return {
        "approved": approved or [],
        "initiatives": [{"id": "i", "started": True, "ready_tasks": [], "landed": set()}],
        "phase_tasks": [{"initiative": "i", "phase": "p", "tasks": tasks}],
    }


def _approved(id: str, done: bool) -> dict:
    return {
        "id": id, "initiative": "i", "repo": "r", "phase": "p", "phase_done": done, "needs": [], "run": "i-1",
        "needs_fetch": False,
    }


def test_approved_beside_quarantined_gives_a_partial_land_naming_the_approved_task():
    assert partial_land_action("i", "p", [("a", "approved"), ("b", "quarantined")]) == {
        "kind": "partial_land", "initiative": "i", "phase": "p", "task_ids": ["a"], "quarantined": 1,
    }


def test_approved_ids_keep_the_phase_order_and_done_and_dropped_siblings_do_not_block():
    tasks = [("c", "approved"), ("d", "done"), ("a", "approved"), ("e", "dropped"), ("q", "quarantined")]
    assert partial_land_action("i", "p", tasks) == {
        "kind": "partial_land", "initiative": "i", "phase": "p", "task_ids": ["c", "a"], "quarantined": 1,
    }


def test_approved_beside_a_running_task_gives_no_land_action():
    assert plan_lands(_facts([("a", "approved"), ("b", "running")], [_approved("a", False)])) == []


def test_a_quarantined_task_does_not_excuse_a_running_one():
    assert partial_land_action("i", "p", [("a", "approved"), ("b", "quarantined"), ("c", "running")]) is None


def test_a_pending_sibling_blocks_and_an_in_review_sibling_blocks():
    assert classify_phase([("a", "approved"), ("b", "quarantined"), ("c", "ready")]) == "wait"
    assert classify_phase([("a", "approved"), ("b", "quarantined"), ("c", "review")]) == "wait"


def test_an_all_approved_phase_still_gives_the_whole_phase_land():
    facts = _facts([("a", "approved"), ("b", "approved")], [_approved("a", True), _approved("b", True)])
    assert [a["kind"] for a in plan_lands(facts)] == ["land_phase"]


def test_a_quarantined_only_phase_gives_no_partial_land():
    assert partial_land_action("i", "p", [("a", "quarantined"), ("b", "quarantined")]) is None
    assert plan_lands(_facts([("a", "quarantined"), ("b", "done")])) == []


def test_plan_lands_emits_the_partial_land_for_a_quarantined_sibling():
    assert plan_lands(_facts([("a", "approved"), ("b", "quarantined")], [_approved("a", False)])) == [
        {
            "kind": "partial_land", "initiative": "i", "phase": "p", "task_ids": ["a"], "quarantined": 1,
            "repo": "r", "run": "i-1", "task_runs": {"a": "i-1"},
        }
    ]


def test_the_edge_lists_every_task_state_of_a_phase_with_approved_work():
    items = [
        {"id": "a", "initiative": "i", "phase": "p", "state": "approved"},
        {"id": "b", "initiative": "i", "phase": "p", "state": "quarantined"},
        {"id": "c", "initiative": "i", "phase": "q", "state": "ready"},
    ]
    assert phase_task_rows(items) == [{"initiative": "i", "phase": "p", "tasks": [("a", "approved"), ("b", "quarantined")]}]


def _item(id: str, state: str) -> dict:
    return {"id": id, "initiative": "i", "phase": "p", "state": state}


def _planned(items: list[dict], landed: set[str]) -> list[dict]:
    rows = phase_task_rows(mark_landed(items, landed))
    approved = [_approved(i["id"], False) for i in items if i["state"] == "approved"]
    return plan_lands({**_facts([]), "approved": approved, "phase_tasks": rows})


def test_the_only_approved_task_being_landed_beside_a_quarantined_one_plans_no_partial_land():
    assert _planned([_item("a", "approved"), _item("b", "quarantined")], {"a"}) == []


def test_a_landed_approved_task_is_left_out_of_the_partial_land_task_ids():
    items = [_item("a", "approved"), _item("b", "approved"), _item("q", "quarantined")]
    assert [a["task_ids"] for a in _planned(items, {"a"})] == [["b"]]


def test_mark_landed_marks_only_the_landed_approved_ids():
    items = [_item("a", "approved"), _item("b", "approved"), _item("c", "ready")]
    assert [i["state"] for i in mark_landed(items, {"a", "c"})] == ["done", "approved", "ready"]


# --- the run an approved task lands from, when the initiative has run again ---

_ITEMS = [
    {"id": "a", "initiative": "i", "phase": "p", "state": "approved"},
    {"id": "q", "initiative": "i", "phase": "p", "state": "quarantined"},
]


def _record(run: str, task: str, verdict: str = "approve", cause: str = "") -> dict:
    """A task record as `_chair_stranded_inputs` reads it; `cause` is the quarantine kind the run gave it, if any."""
    return {"run": run, "task": task, "phase": "p", "review": {"verdict": verdict}, "landed": False,
            **({"cause": cause} if cause else {})}


def _task_runs(records: list[dict], unsettled: frozenset[str] = frozenset()) -> dict:
    """The planner input: the partial_land's task_runs, from the records through the same rows the chair reads."""
    tasks = with_runs([{**_approved("a", False), "run": ""}], stranded(land_records(records, unsettled), _ITEMS))
    return plan_lands(_facts([("a", "approved"), ("q", "quarantined")], tasks))[0]["task_runs"]


def test_a_task_approved_in_run_n_keeps_run_n_after_run_n_plus_1_of_the_initiative():
    # i-1 approved a, then quarantined it on its full checks; the chair approved it over that, and i-2 re-ran q.
    records = [_record("i-1", "a", cause="code"), _record("i-1", "q", "revise", "review"), _record("i-2", "q", "revise", "code")]
    assert _task_runs(records) == {"a": "i-1"}


def test_with_only_run_n_the_map_is_unchanged():
    assert _task_runs([_record("i-1", "a"), _record("i-1", "q", "revise", "review")]) == {"a": "i-1"}


def test_a_task_approved_in_no_run_has_no_run():
    assert _task_runs([_record("i-1", "a", "revise", "review"), _record("i-2", "q", "revise", "code")]) == {"a": ""}


def test_a_newer_run_still_out_keeps_the_superseded_approval_from_landing():
    # i-2 is live or remote and unfetched: it may hold the approval a now reads, so a waits for it.
    assert _task_runs([_record("i-1", "a", cause="code")], frozenset({"i-2"})) == {"a": ""}
