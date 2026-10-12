from datetime import UTC, datetime, timedelta

from agent_tools.chair_exec import Deps, perform
from agent_tools.chair_facts import land_refusals_from_actions
from agent_tools.chair_plan_land import plan_lands
from agent_tools.partial_land import backup_ref, land_action, perform_partial_land, siblings, steps, to_restore

LANDED = "merge: ok\nmark_done: ok\n"


def _action(task_ids=("a",), runs=None, quarantined=1) -> dict:
    runs = dict.fromkeys(task_ids, "i-1") if runs is None else runs
    return {
        "kind": "partial_land",
        "initiative": "i",
        "phase": "p",
        "task_ids": list(task_ids),
        "quarantined": quarantined,
        "repo": "/r",
        "run": "i-1",
        "task_runs": runs,
        "epoch": 3,
    }


class FakeRepo:
    """Branches in a dict; `land` deletes every agents/<run>/* branch, as `cox runs land` does, and logs each event."""

    def __init__(self, branches: dict[str, str]) -> None:
        self.branches = dict(branches)
        self.refs: dict[str, str] = {}
        self.events: list[tuple[str, str]] = []

    def git(self, argv: list[str]) -> tuple[int, str]:
        args = argv[3:]
        if args[0] == "for-each-ref":
            prefix = args[2].removeprefix("refs/heads/")
            return 0, "".join(f"{b} {sha}\n" for b, sha in self.branches.items() if b.startswith(prefix))
        if args[:2] == ["update-ref", "-d"]:
            self.refs.pop(args[2], None)
            return 0, ""
        if args[0] == "update-ref":
            self.refs[args[1]] = args[2]
            self.events.append(("backup", args[1].rsplit("/", 1)[-1]))
            return 0, ""
        if args[0] == "branch":
            self.branches[args[1]] = args[2]
            self.events.append(("restore", args[1].rsplit("/", 1)[-1]))
            return 0, ""
        raise AssertionError(argv)

    def wipe(self, run: str) -> None:
        self.branches = {b: s for b, s in self.branches.items() if not b.startswith(f"agents/{run}/")}


def _lander(repo: FakeRepo, results: dict[str, dict] | None = None):
    def land(action: dict) -> dict:
        repo.events.append(("land", action["task_id"]))
        repo.wipe(action["run"])
        return (results or {}).get(action["task_id"], {"status": "landed", "reason": LANDED})

    return land


# --- the core ---


def test_steps_back_up_land_and_restore_each_approved_task_in_order():
    assert [(s["kind"], s["task_id"]) for s in steps(_action(("a", "c")))] == [
        ("backup", "a"),
        ("land", "a"),
        ("restore", "a"),
        ("backup", "c"),
        ("land", "c"),
        ("restore", "c"),
    ]


def test_a_task_with_no_known_run_ends_the_steps():
    assert [s["task_id"] for s in steps(_action(("a", "b", "c"), {"a": "i-1", "b": "", "c": "i-1"}))] == ["a", "a", "a"]
    assert steps(_action(("a",), {"a": ""})) == []


def test_the_land_action_names_the_task_its_run_and_the_repo():
    assert land_action(_action(("a",), {"a": "i-2"}), "a") == {
        "kind": "land",
        "initiative": "i",
        "phase": "p",
        "task_id": "a",
        "repo": "/r",
        "run": "i-2",
        "epoch": 3,
    }


def test_siblings_are_the_other_agents_branches_of_the_run():
    branches = {"agents/i-1/a": "1", "agents/i-1/q": "2", "agents/i-2/x": "3", "epic/i/p": "4"}
    assert siblings("i-1", "a", branches) == {"q": "2"}
    assert to_restore({"q": "2", "r": "5"}, {"agents/i-1/r": "5"}, "i-1") == {"q": "2"}


# --- the edge, with fakes ---


def test_one_approved_and_one_quarantined_backs_up_lands_and_restores_once():
    repo = FakeRepo({"agents/i-1/a": "aaa", "agents/i-1/q": "qqq"})
    result = perform_partial_land(_action(("a",)), repo.git, _lander(repo))
    assert result["status"] == "landed"
    assert repo.events == [("backup", "q"), ("land", "a"), ("restore", "q")]
    assert repo.branches == {"agents/i-1/q": "qqq"}  # the quarantined task's draft is back, and never landed
    assert repo.refs == {}  # the backup refs are dropped once restored


def test_restore_still_runs_when_the_land_fails_and_later_tasks_wait():
    repo = FakeRepo({"agents/i-1/a": "aaa", "agents/i-1/c": "ccc", "agents/i-1/q": "qqq"})
    refusal = {"kind": "needs_chair", "initiative": "i", "task_id": "a", "cause": "checks"}
    failed = {"a": {"status": "refused", "reason": "failing checks: test", "needs_chair": refusal}}
    result = perform_partial_land(_action(("a", "c")), repo.git, _lander(repo, failed))
    assert result["status"] == "refused"
    assert result["needs_chair"] == refusal
    assert [e for e in repo.events if e[0] == "land"] == [("land", "a")]
    assert repo.branches == {
        "agents/i-1/c": "ccc",
        "agents/i-1/q": "qqq",
    }  # c waits for the next tick on its own branch


def test_restore_still_runs_when_the_land_raises():
    repo = FakeRepo({"agents/i-1/a": "aaa", "agents/i-1/q": "qqq"})

    def land(action):
        repo.wipe("i-1")
        raise RuntimeError("boom")

    result = perform_partial_land(_action(("a",)), repo.git, land)
    assert result["status"] == "failed"
    assert "boom" in result["reason"]
    assert repo.branches == {"agents/i-1/q": "qqq"}


def test_two_approved_tasks_land_in_order_each_keeping_the_other():
    repo = FakeRepo({"agents/i-1/a": "aaa", "agents/i-1/c": "ccc", "agents/i-1/q": "qqq"})
    result = perform_partial_land(_action(("a", "c")), repo.git, _lander(repo))
    assert result["status"] == "landed"
    assert [e for e in repo.events if e[0] == "land"] == [("land", "a"), ("land", "c")]
    assert repo.branches == {"agents/i-1/q": "qqq"}
    assert "landed a, c" in result["reason"]


def test_every_landed_task_commit_rides_in_the_result_not_only_the_last():
    repo = FakeRepo({"agents/i-1/a": "aaa", "agents/i-1/c": "ccc", "agents/i-1/q": "qqq"})
    lands = {
        "a": {"status": "landed", "reason": "pr_create: https://github.com/o/r/pull/1\n" + LANDED, "commit": "c-a"},
        "c": {"status": "landed", "reason": "pr_create: https://github.com/o/r/pull/2\n" + LANDED, "commit": "c-c"},
    }
    result = perform_partial_land(_action(("a", "c")), repo.git, _lander(repo, lands))
    assert result["status"] == "landed"
    assert result["commit"] == "c-c"  # the single-commit field a plain land carries is unchanged
    assert [(c["task_id"], c["commit"]) for c in result["landed_commits"]] == [("a", "c-a"), ("c", "c-c")]
    assert "pull/1" in result["landed_commits"][0]["reason"]


def test_a_task_landed_before_a_later_failure_keeps_its_commit():
    repo = FakeRepo({"agents/i-1/a": "aaa", "agents/i-1/c": "ccc"})
    lands = {
        "a": {"status": "landed", "reason": LANDED, "commit": "c-a"},
        "c": {"status": "not_landed", "reason": "checks red"},
    }
    result = perform_partial_land(_action(("a", "c")), repo.git, _lander(repo, lands))
    assert result["status"] == "not_landed"
    assert [(c["task_id"], c["commit"]) for c in result["landed_commits"]] == [("a", "c-a")]


def test_a_failed_backup_runs_no_land_and_leaves_every_branch_in_place():
    branches = {"agents/i-1/a": "aaa", "agents/i-1/c": "ccc", "agents/i-1/q": "qqq"}
    repo = FakeRepo(branches)
    plain = repo.git

    def git(argv):
        return (1, "fatal: cannot lock ref") if argv[3:5] == ["update-ref", backup_ref("i-1", "q")] else plain(argv)

    result = perform_partial_land(_action(("a",)), git, _lander(repo))
    assert result["status"] == "failed"
    assert "backing up agents/i-1/q" in result["reason"]
    assert not any(e[0] == "land" for e in repo.events)
    assert repo.branches == branches
    assert repo.refs == {}  # the siblings backed up before the failure are dropped again
    assert "landed_commits" not in result


def test_a_failed_backup_on_a_later_task_keeps_the_earlier_land():
    repo = FakeRepo({"agents/i-1/a": "aaa", "agents/i-1/c": "ccc", "agents/i-1/q": "qqq"})
    plain = repo.git
    lands = {"a": {"status": "landed", "reason": LANDED, "commit": "c-a"}}

    def git(argv):
        landed_a = ("land", "a") in repo.events
        return (1, "fatal: no") if landed_a and argv[3] == "update-ref" and argv[4] != "-d" else plain(argv)

    result = perform_partial_land(_action(("a", "c")), git, _lander(repo, lands))
    assert result["status"] == "failed"
    assert [e for e in repo.events if e[0] == "land"] == [("land", "a")]
    assert result["reason"].startswith("landed a")
    assert [c["commit"] for c in result["landed_commits"]] == ["c-a"]


def test_a_failed_restore_keeps_the_backup_ref():
    repo = FakeRepo({"agents/i-1/a": "aaa", "agents/i-1/q": "qqq"})
    plain = repo.git

    def git(argv):
        return (1, "fatal: no") if argv[3] == "branch" else plain(argv)

    result = perform_partial_land(_action(("a",)), git, _lander(repo))
    assert result["status"] == "failed"
    assert repo.refs == {backup_ref("i-1", "q"): "qqq"}


def test_no_known_run_is_recorded_and_lands_nothing():
    repo = FakeRepo({"agents/i-1/a": "aaa"})
    result = perform_partial_land(_action(("a",), {"a": ""}), repo.git, _lander(repo))
    assert result["status"] == "recorded"
    assert repo.events == []


# --- the loop: plan, then perform ---


def _facts(tasks, approved) -> dict:
    return {
        "approved": approved,
        "initiatives": [{"id": "i", "started": True, "ready_tasks": [], "landed": set()}],
        "phase_tasks": [{"initiative": "i", "phase": "p", "tasks": tasks}],
    }


def _approved(task_id: str) -> dict:
    return {
        "id": task_id,
        "initiative": "i",
        "repo": "/r",
        "phase": "p",
        "phase_done": False,
        "needs": [],
        "run": "i-1",
        "needs_fetch": False,
    }


def _deps(calls: list, repo: FakeRepo, land_output: str = LANDED, land_code: int = 0) -> Deps:
    def run(argv):
        calls.append(argv)
        if argv[:3] == ["cox", "runs", "land"]:
            repo.events.append(("land", argv[argv.index("--task") + 1]))
            repo.wipe(argv[3])
            return land_code, land_output
        return repo.git(argv)

    return Deps(
        run=run,
        delete_branches=lambda repo_, pattern: ([], ""),
        acquire_lease=lambda holder, host: "",
        record=lambda action: calls.append(("record", action["kind"], action.get("status"))),
        run_id=lambda action: "i-1",
        repo_for=lambda action: action.get("repo", "/r"),
    )


def test_the_loop_lands_the_approved_task_and_leaves_the_quarantined_one():
    actions = plan_lands(_facts([("a", "approved"), ("q", "quarantined")], [_approved("a")]))
    repo = FakeRepo({"agents/i-1/a": "aaa", "agents/i-1/q": "qqq"})
    calls: list = []
    results = perform([{**a, "epoch": 1} for a in actions], _deps(calls, repo), lambda: 1, dry_run=False)
    lands = [c for c in calls if c[:3] == ["cox", "runs", "land"]]
    assert lands == [["cox", "runs", "land", "i-1", "--task", "a", "--repo", "/r", "--apply", "--no-claim"]]
    assert repo.events == [("backup", "q"), ("land", "a"), ("restore", "q")]
    assert [r["status"] for r in results] == ["landed"]
    assert ("record", "partial_land", "landed") in calls


def test_a_refused_task_land_is_escalated_and_the_sibling_restored():
    actions = plan_lands(_facts([("a", "approved"), ("q", "quarantined")], [_approved("a")]))
    repo = FakeRepo({"agents/i-1/a": "aaa", "agents/i-1/q": "qqq"})
    calls: list = []
    results = perform(
        [{**a, "epoch": 1} for a in actions], _deps(calls, repo, "failing checks: test", 1), lambda: 1, dry_run=False
    )
    assert [r["status"] for r in results] == ["refused", "escalated"]
    assert results[1]["action"]["cause"] == "checks"
    assert "agents/i-1/q" in repo.branches


def test_an_approved_task_beside_a_running_one_plans_nothing_so_the_loop_does_nothing():
    actions = plan_lands(_facts([("a", "approved"), ("b", "running")], [_approved("a")]))
    assert actions == []
    calls: list = []
    assert perform(actions, _deps(calls, FakeRepo({})), lambda: 1, dry_run=False) == []
    assert calls == []


def test_a_remote_unfetched_run_is_fetched_before_the_partial_land():
    row = {**_approved("a"), "needs_fetch": True}
    kinds = [a["kind"] for a in plan_lands(_facts([("a", "approved"), ("q", "quarantined")], [row]))]
    assert kinds == ["fetch", "partial_land"]


def test_a_refused_partial_land_backs_off_like_a_land_phase():
    now = datetime(2026, 10, 12, 12, tzinfo=UTC)
    rows = [
        {
            "kind": "partial_land",
            "initiative": "i",
            "phase": "p",
            "run": "i-1",
            "status": "refused",
            "ts": (now - timedelta(minutes=1)).isoformat(),
        }
    ]
    facts = {
        **_facts([("a", "approved"), ("q", "quarantined")], [_approved("a")]),
        "land_refusals": land_refusals_from_actions(rows, now),
    }
    assert plan_lands(facts, now) == []
    assert [a["kind"] for a in plan_lands(facts, now + timedelta(hours=3))] == ["partial_land"]
