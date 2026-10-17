import copy
from datetime import UTC, datetime

from agent_tools.chair_plan import plan_tick
from agent_tools.chair_plan_recover import plan_recover, refused_launches, refused_relaunches
from agent_tools.chair_read_quarantined import RUNAWAY_CAUSE
from agent_tools.chair_types import ApprovedTask, Facts, InitiativeFacts, LandingFact, QuarantineFacts


def _initiative(started: bool = True, ready: list[dict] | None = None, landed: set[str] | None = None) -> InitiativeFacts:
    return {
        "id": "i",
        "started": started,
        "ready_tasks": [{"id": "t1", "needs": ["a"]}] if ready is None else ready,  # type: ignore[typeddict-item]
        "landed": {"a"} if landed is None else landed,
    }


def _quarantine(
    cause: str = "harness",
    failures: int = 1,
    initiative: str = "i",
    task_id: str = "q1",
    has_patch: bool = False,
    rescue_failed: bool = False,
) -> QuarantineFacts:
    return {
        "task_id": task_id,
        "initiative": initiative,
        "cause": cause,
        "harness_failures": failures,
        "has_patch": has_patch,
        "rescue_failed": rescue_failed,
    }


def _approved(task_id: str = "q1", phase_done: bool = False, run: str = "r1", initiative: str = "i") -> ApprovedTask:
    return {
        "id": task_id,
        "initiative": initiative,
        "repo": "repo",
        "phase": "p",
        "phase_done": phase_done,
        "needs": [],
        "run": run,
        "needs_fetch": False,
    }


def _facts(
    initiatives: list[InitiativeFacts],
    quarantines: list[QuarantineFacts],
    approved: list[ApprovedTask] | None = None,
    landing: list[LandingFact] | None = None,
) -> Facts:
    return {
        **({} if landing is None else {"landing": landing}),
        "lease": {"holder": "a", "host": "h", "epoch": 1, "mine": True, "released": False, "stale": False},
        "limits": {"hard_stop": False, "weekly_fraction": 0.1, "hard_stop_fraction": 0.9, "launch_cap": 2, "go_degraded": False},
        "dispatch": {"max_in_flight": 3, "live_runs": 0},
        "approved": [] if approved is None else approved,
        "initiatives": initiatives,
        "quarantines": quarantines,
        "intake": [],
        "work_store_ready": True,
        "sources_configured": True,
    }


def test_a_started_initiative_with_all_needs_landed_is_cleared_then_relaunched():
    assert plan_recover(_facts([_initiative()], [])) == [
        {"kind": "clear_branches", "initiative": "i"},
        {"kind": "relaunch", "initiative": "i"},
    ]


def test_a_harness_quarantine_with_one_failure_gets_one_retry():
    assert plan_recover(_facts([], [_quarantine()])) == [{"kind": "retry", "task_id": "q1", "initiative": "i"}]


def test_a_harness_quarantine_with_a_patch_gets_one_rescue():
    assert plan_recover(_facts([], [_quarantine(has_patch=True)])) == [
        {"kind": "rescue", "initiative": "i", "task_id": "q1"}
    ]


def test_a_failed_rescue_needs_the_chair_and_gets_no_rescue_or_retry():
    facts = _facts([], [_quarantine(has_patch=True, rescue_failed=True)])
    assert plan_recover(facts) == [{"kind": "needs_chair", "initiative": "i", "task_id": "q1", "cause": "harness"}]


def test_two_harness_failures_with_a_patch_are_not_rescued():
    facts = _facts([], [_quarantine(failures=2, has_patch=True)])
    assert plan_recover(facts) == [{"kind": "needs_chair", "initiative": "i", "task_id": "q1", "cause": "harness"}]


def test_a_non_harness_cause_with_a_patch_is_not_rescued():
    facts = _facts([], [_quarantine(cause="verify", has_patch=True)])
    assert plan_recover(facts) == [{"kind": "needs_chair", "initiative": "i", "task_id": "q1", "cause": "verify"}]


def test_a_runaway_cause_always_needs_the_chair_even_with_zero_failures():
    facts = _facts([], [_quarantine(cause=RUNAWAY_CAUSE, failures=0)])
    assert plan_recover(facts) == [{"kind": "needs_chair", "initiative": "i", "task_id": "q1", "cause": "runaway"}]


def test_a_rescued_initiative_is_not_relaunched_this_tick():
    facts = _facts([_initiative()], [_quarantine(has_patch=True)])
    assert plan_recover(facts) == [{"kind": "rescue", "initiative": "i", "task_id": "q1"}]


def test_a_non_harness_cause_needs_the_chair_and_blocks_relaunch():
    facts = _facts([_initiative()], [_quarantine(cause="verify")])
    assert plan_recover(facts) == [{"kind": "needs_chair", "initiative": "i", "task_id": "q1", "cause": "verify"}]


def test_two_harness_failures_need_the_chair_and_block_relaunch():
    facts = _facts([_initiative()], [_quarantine(failures=2)])
    assert plan_recover(facts) == [{"kind": "needs_chair", "initiative": "i", "task_id": "q1", "cause": "harness"}]


def test_an_initiative_that_is_not_started_is_never_relaunched():
    assert plan_recover(_facts([_initiative(started=False)], [])) == []


def test_an_unlanded_need_blocks_relaunch():
    assert plan_recover(_facts([_initiative(landed=set())], [])) == [
        {"kind": "needs_chair", "initiative": "i", "cause": "waiting on a"}
    ]


def test_an_initiative_with_no_ready_tasks_is_not_relaunched():
    assert plan_recover(_facts([_initiative(ready=[])], [])) == []


def test_a_ready_task_whose_only_need_is_a_blocked_task_is_not_relaunched():
    """The 2026-09-29 case: allocate-short-initiative-ids-from-a-store relaunched four times because its only
    ready task, implement-store-ids-module, needs the unlanded add-id-sequence-schema-migration. Not
    relaunched; reported once as `waiting on add-id-sequence-schema-migration`."""
    facts = _facts(
        [_initiative(ready=[{"id": "implement-store-ids-module", "needs": ["add-id-sequence-schema-migration"]}], landed=set())],
        [],
    )
    assert plan_recover(facts) == [
        {"kind": "needs_chair", "initiative": "i", "cause": "waiting on add-id-sequence-schema-migration"}
    ]


def test_the_same_initiative_relaunches_once_that_need_is_done():
    facts = _facts(
        [
            _initiative(
                ready=[{"id": "implement-store-ids-module", "needs": ["add-id-sequence-schema-migration"]}],
                landed={"add-id-sequence-schema-migration"},
            )
        ],
        [],
    )
    assert plan_recover(facts) == [
        {"kind": "clear_branches", "initiative": "i"},
        {"kind": "relaunch", "initiative": "i"},
    ]


def test_a_retried_initiative_is_not_relaunched_this_tick():
    facts = _facts([_initiative()], [_quarantine()])
    assert plan_recover(facts) == [{"kind": "retry", "task_id": "q1", "initiative": "i"}]


def test_a_needs_chair_quarantine_drops_a_retry_on_the_same_initiative():
    facts = _facts([], [_quarantine(task_id="q1"), _quarantine(cause="verify", task_id="q2")])
    assert plan_recover(facts) == [{"kind": "needs_chair", "initiative": "i", "task_id": "q2", "cause": "verify"}]


def test_a_stranded_quarantine_matching_an_approved_row_plans_no_action_whatever_its_phase_done():
    stranded = [_quarantine(cause="stranded", task_id="q1")]
    assert plan_recover(_facts([], stranded, approved=[_approved(task_id="q1", phase_done=False)])) == []
    assert plan_recover(_facts([], stranded, approved=[_approved(task_id="q1", phase_done=True)])) == []


def test_a_stranded_quarantine_with_no_matching_approved_row_still_needs_the_chair():
    facts = _facts([], [_quarantine(cause="stranded", task_id="q1")], approved=[_approved(task_id="other")])
    assert plan_recover(facts) == [{"kind": "needs_chair", "initiative": "i", "task_id": "q1", "cause": "stranded"}]


def test_a_stranded_quarantine_whose_task_id_is_approved_only_in_another_initiative_still_needs_the_chair():
    facts = _facts([], [_quarantine(cause="stranded", task_id="q1")], approved=[_approved(task_id="q1", initiative="j")])
    assert plan_recover(facts) == [{"kind": "needs_chair", "initiative": "i", "task_id": "q1", "cause": "stranded"}]


def test_a_non_stranded_cause_matching_an_approved_row_still_needs_the_chair():
    facts = _facts([], [_quarantine(cause="verify", task_id="q1")], approved=[_approved(task_id="q1")])
    assert plan_recover(facts) == [{"kind": "needs_chair", "initiative": "i", "task_id": "q1", "cause": "verify"}]


def test_plan_recover_leaves_the_facts_unchanged():
    facts = _facts([_initiative()], [_quarantine(cause="verify", initiative="j")])
    before = copy.deepcopy(facts)
    plan_recover(facts)
    assert facts == before


def test_a_stranded_approved_quarantine_does_not_block_relaunch_when_ready():
    facts = _facts([_initiative()], [_quarantine(cause="stranded", task_id="q1")], approved=[_approved(task_id="q1")])
    assert plan_recover(facts) == [
        {"kind": "clear_branches", "initiative": "i"},
        {"kind": "relaunch", "initiative": "i"},
    ]


def test_a_second_non_none_quarantine_on_the_same_initiative_still_blocks_relaunch():
    facts = _facts(
        [_initiative()],
        [_quarantine(cause="stranded", task_id="q1"), _quarantine(cause="verify", task_id="q2")],
        approved=[_approved(task_id="q1")],
    )
    assert plan_recover(facts) == [{"kind": "needs_chair", "initiative": "i", "task_id": "q2", "cause": "verify"}]


def test_a_stranded_approved_quarantine_alone_does_not_relaunch_without_ready_tasks():
    facts = _facts(
        [_initiative(ready=[])], [_quarantine(cause="stranded", task_id="q1")], approved=[_approved(task_id="q1")]
    )
    assert plan_recover(facts) == []


def _in_repo(initiative: str, repo: str) -> InitiativeFacts:
    return {**_initiative(), "id": initiative, "repo": repo}


_PAIRS = [
    {"kind": "clear_branches", "initiative": "a"},
    {"kind": "relaunch", "initiative": "a"},
    {"kind": "clear_branches", "initiative": "b"},
    {"kind": "relaunch", "initiative": "b"},
]


def test_an_initiative_whose_repo_is_landing_gets_no_relaunch_pair():
    landing: list[LandingFact] = [{"initiative": "x", "phase": "p", "repo": "r1"}]
    facts = _facts([_in_repo("a", "r1"), _in_repo("b", "r2")], [], landing=landing)
    assert plan_recover(facts) == _PAIRS[2:]


def test_an_empty_landing_list_relaunches_both_initiatives():
    assert plan_recover(_facts([_in_repo("a", "r1"), _in_repo("b", "r2")], [], landing=[])) == _PAIRS


def test_facts_with_no_landing_key_relaunch_both_initiatives():
    facts = _facts([_in_repo("a", "r1"), _in_repo("b", "r2")], [])
    assert "landing" not in facts
    assert plan_recover(facts) == _PAIRS


def _triaged(cause: str, reasons: list[str]) -> QuarantineFacts:
    """A quarantine with one attempt per reason on its current body, oldest first."""
    return {
        **_quarantine(cause=cause, failures=0),
        "causes": [cause] * len(reasons),
        "reasons": reasons,
        "reason": reasons[-1],
        "run": "r-9",
        "repo": "/repo",
    }  # type: ignore[typeddict-unknown-key]


_ONE_PAIR = [{"kind": "clear_branches", "initiative": "i"}, {"kind": "relaunch", "initiative": "i"}]


def test_one_ticket_quarantine_on_the_current_body_plans_a_relaunch():
    assert plan_recover(_facts([_initiative()], [_triaged("ticket", ["no goal"])])) == _ONE_PAIR


def test_one_code_quarantine_on_the_current_body_plans_a_relaunch():
    assert plan_recover(_facts([_initiative()], [_triaged("code", ["import error"])])) == _ONE_PAIR


def test_a_second_ticket_quarantine_on_the_same_body_needs_the_chair_with_both_reasons():
    (action,) = plan_recover(_facts([_initiative()], [_triaged("ticket", ["no goal stated", "surface missing"])]))
    assert (action["kind"], action["remedy"]) == ("needs_chair", "re-ground")
    assert "no goal stated" in action["reason"] and "surface missing" in action["reason"]


def test_a_second_code_quarantine_on_the_same_body_needs_the_chair_with_both_reasons():
    (action,) = plan_recover(_facts([_initiative()], [_triaged("code", ["import error", "test fails"])]))
    assert (action["kind"], action["remedy"]) == ("needs_chair", "re-ground")
    assert "import error" in action["reason"] and "test fails" in action["reason"]


def test_a_ticket_quarantine_listing_no_attempts_still_needs_the_chair():
    facts = _facts([_initiative()], [_quarantine(cause="ticket", failures=0)])
    assert plan_recover(facts) == [{"kind": "needs_chair", "initiative": "i", "task_id": "q1", "cause": "ticket"}]


def test_harness_main_red_stranded_and_runaway_quarantines_plan_as_before():
    assert plan_recover(_facts([], [_quarantine()])) == [{"kind": "retry", "task_id": "q1", "initiative": "i"}]
    assert plan_recover(_facts([_initiative()], [_quarantine(cause="main_red")])) == _ONE_PAIR
    stranded = plan_recover(_facts([], [_quarantine(cause="stranded", failures=0)]))
    assert stranded == [{"kind": "needs_chair", "initiative": "i", "task_id": "q1", "cause": "stranded"}]
    runaway = plan_recover(_facts([], [_triaged(RUNAWAY_CAUSE, ["ceiling"])]))
    assert [a["kind"] for a in runaway] == ["needs_chair"]


_NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def _with_lanes(max_in_flight: int, hosts: list[dict]) -> Facts:
    tick_keys = {
        "remote_unfetched": {},
        "run_exited": {"i": True},
        "schema_deaths": {},
        "lost_runs": {},
        "last_housekeeping_at": None,
        "housekeeping_hours": 24.0,
        "stale_days": 7,
        "stale_candidates": [],
        "stall_candidates": [],
        "home": {},
        "dispatch": {"max_in_flight": max_in_flight, "live_runs": 0, "hosts": hosts},
    }
    return {**_facts([_initiative()], []), **tick_keys}  # type: ignore[typeddict-item,return-value]


def _kinds_for(actions: list[dict], initiative: str) -> set[str]:
    return {a["kind"] for a in actions if a.get("initiative") == initiative}


def test_zero_lane_capacity_refuses_the_relaunch_and_plan_tick_does_not_launch_it_instead():
    facts = _with_lanes(0, [{"name": "h1", "live_runs": 0, "capacity": 0}])
    assert refused_launches(facts, _NOW) == frozenset({"i"})
    assert not _kinds_for(plan_tick(facts, _NOW), "i") & {"relaunch", "launch_epic"}


def test_a_host_with_capacity_still_relaunches():
    facts = _with_lanes(0, [{"name": "h1", "live_runs": 0, "capacity": 0}, {"name": "h2", "live_runs": 0, "capacity": 2}])
    assert refused_launches(facts, _NOW) == frozenset()
    assert "relaunch" in _kinds_for(plan_tick(facts, _NOW), "i")


def test_a_host_row_with_no_capacity_is_uncapped():
    facts = _with_lanes(0, [{"name": "h1", "live_runs": 0}])
    assert refused_launches(facts, _NOW) == frozenset()


def test_without_now_lane_capacity_refuses_nothing():
    facts = _with_lanes(0, [{"name": "h1", "live_runs": 0, "capacity": 0}])
    assert refused_launches(facts, None) == frozenset()
    assert refused_relaunches(facts, None, set()) == {}
