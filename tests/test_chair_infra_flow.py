"""End to end over recorded `gh` payloads: a setup-only failure is rerun, a real failure is reverted, a walk-back spares the older land.

main_ci, the facts builder, the plan and the executor dispatch run together over a fake runner and fake ports.
Nothing here goes through cli.py or spawns a process.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from agent_tools import chair_ci_jobs, chair_revert_green, chair_revert_ports
from agent_tools.chair_exec import Deps, perform
from agent_tools.chair_facts import gather_facts
from agent_tools.chair_plan import plan_tick
from agent_tools.chair_revert_exec import rerun_reds
from agent_tools.chair_types import LandWatch
from tests.test_chair_facts import NOW
from tests.test_chair_facts import _deps as _facts_deps

EPOCH = 4  # the lease epoch `tests.test_chair_facts._deps` plans under
PR_URL = "https://forge.test/r/pulls/9"
REPO = "/r"
LOG = "FAILED tests/test_x.py::test_y - AssertionError"

A, B, C = "aaa", "bbb", "ccc"
WATCH_A: LandWatch = {"initiative": "ia", "phase": "p1", "repo": REPO, "pr": 7, "commit": A}
WATCH_B: LandWatch = {"initiative": "ib", "phase": "p1", "repo": REPO, "pr": 8, "commit": B}
REVERT_CALLS = ("fetch_main", "revert_onto_branch", "push", "open_pr", "wait_checks", "merge", "quarantine", "resolve")


def _run(run_id: int, sha: str, conclusion: str | None, attempt: int = 1, status: str = "completed") -> dict:
    """A `gh run list --json databaseId,status,conclusion,headSha,attempt` row."""
    return {"databaseId": run_id, "status": status, "conclusion": conclusion, "headSha": sha, "attempt": attempt}


SETUP_ONLY_JOBS = {
    "jobs": [
        {
            "name": "test",
            "status": "completed",
            "conclusion": "failure",
            "steps": [{"name": "Set up job", "status": "completed", "conclusion": None, "number": 1}],
        }
    ]
}

REAL_FAILURE_JOBS = {
    "jobs": [
        {
            "name": "test",
            "status": "completed",
            "conclusion": "failure",
            "steps": [
                {"name": "Set up job", "status": "completed", "conclusion": "success", "number": 1},
                {"name": "Run tests", "status": "completed", "conclusion": "failure", "number": 2},
            ],
        }
    ]
}


class FakeRunner:
    """Answers the gh and git argv the revert ports build, from recorded payloads; any other argv fails the test."""

    def __init__(
        self,
        runs: dict[str, list[dict]],
        jobs: dict[int, dict] | None = None,
        reruns: dict[int, tuple[dict, dict]] | None = None,
        ancestry: frozenset[tuple[str, str]] = frozenset(),
    ) -> None:
        self.runs = {commit: list(rows) for commit, rows in runs.items()}
        self.jobs = dict(jobs or {})
        self.reruns = dict(reruns or {})  # run id to the (row, jobs) that appears once it is rerun
        self.ancestry = ancestry
        self.argvs: list[list[str]] = []

    def __call__(self, argv: list[str]) -> tuple[int, str]:
        self.argvs.append(list(argv))
        head = argv[:3]
        if head == ["gh", "run", "list"] and "--commit" in argv:
            return 0, json.dumps(self.runs.get(argv[argv.index("--commit") + 1], []))
        if head == ["gh", "run", "list"]:
            assert argv[argv.index("--json") + 1] == "headSha,status,conclusion"
            return 0, json.dumps([row for rows in self.runs.values() for row in rows])
        if head == ["gh", "run", "view"] and argv[-2:] == ["--json", "jobs"]:
            return 0, json.dumps(self.jobs[int(argv[3])])
        if head == ["gh", "run", "view"] and argv[-1] == "--log-failed":
            return 0, LOG
        if head == ["gh", "run", "rerun"]:
            assert argv[-1] == "--failed"
            row, jobs = self.reruns.get(int(argv[3]), (None, None))
            if row is not None:
                self.runs.setdefault(row["headSha"], []).append(row)
                self.jobs[row["databaseId"]] = jobs
            return 0, ""
        if head == ["gh", "run", "watch"]:
            return 0, ""
        if argv[0] == "git" and argv[3:5] == ["merge-base", "--is-ancestor"]:
            return (0 if (argv[5], argv[6]) in self.ancestry else 1), ""
        raise AssertionError(f"unexpected argv {argv}")

    def rerun_issued_for(self, run_id: int) -> bool:
        return any(a[:4] == ["gh", "run", "rerun", str(run_id)] for a in self.argvs)


class FakeGit:
    def __init__(self, calls: list) -> None:
        self.calls = calls

    def fetch_main(self, repo: str) -> None:
        self.calls.append(("fetch_main", repo))

    def revert_onto_branch(self, repo: str, branch: str, commit: str) -> list[str]:
        self.calls.append(("revert_onto_branch", repo, branch, commit))
        return []

    def push(self, repo: str, branch: str) -> None:
        self.calls.append(("push", repo, branch))


class FakeForge:
    def __init__(self, calls: list) -> None:
        self.calls = calls

    def open_pr(self, repo: str, branch: str, title: str, body: str) -> str:
        self.calls.append(("open_pr", repo, branch))
        return PR_URL

    def wait_checks(self, pr: str) -> list[str]:
        self.calls.append(("wait_checks", pr))
        return []

    def merge(self, pr: str) -> None:
        self.calls.append(("merge", pr))


def _facts(runner: FakeRunner, watches: list[LandWatch]) -> dict:
    deps = replace(
        _facts_deps(attempts=(), quarantined=(), stranded=()),
        land_watches=lambda: list(watches),
        read_main_ci=lambda repo, commit: chair_revert_ports.main_ci(runner, repo, commit),
        green_since=lambda repo, commit: chair_revert_green.main_green_since(runner, repo, repo, commit),
        land_hold=lambda: None,
        land_outcomes=lambda: {},
        run_exited=lambda: {},
    )
    return gather_facts(deps, NOW)


def _plan(runner: FakeRunner, watches: list[LandWatch]) -> list:
    return plan_tick(_facts(runner, watches), NOW)


def _perform(actions: list, runner: FakeRunner, calls: list, watches: list[LandWatch] | None = None) -> list:
    """Execute `actions` over fake ports; resolving a land drops its watch from `watches`, as the store would."""
    git, forge = FakeGit(calls), FakeForge(calls)

    def resolve(initiative: str, phase: str, outcome: str) -> None:
        calls.append(("resolve", initiative, phase, outcome))
        if watches is not None:
            watches[:] = [w for w in watches if (w["initiative"], w["phase"]) != (initiative, phase)]

    deps = replace(
        Deps(
            run=lambda argv: calls.append(("run", argv)) or (0, ""),
            delete_branches=lambda repo, pattern: calls.append(("delete", repo, pattern)) or ([], ""),
            acquire_lease=lambda holder, host: calls.append(("lease", holder)) or "",
            record=lambda action: calls.append(("record", action["kind"])),
            run_id=lambda action: "run-1",
            repo_for=lambda action: action.get("repo", "r"),
        ),
        revert_ports=lambda action: (git, forge),
        quarantine_phase=lambda initiative, phase, cause, reason: calls.append(
            ("quarantine", initiative, phase, cause, reason)
        ),
        resolve_land=resolve,
        rerun_main=lambda repo, commit: chair_revert_ports.rerun_infra(runner, repo, commit),
    )
    return perform(actions, deps, lambda: EPOCH, False)


def _of(actions: list, kind: str) -> list:
    return [a for a in actions if a["kind"] == kind]


def _revert_calls(calls: list) -> list:
    return [c for c in calls if c[0] in REVERT_CALLS]


def test_the_recorded_payloads_classify_as_infra_and_real():
    assert chair_ci_jobs.never_started(SETUP_ONLY_JOBS)
    assert not chair_ci_jobs.never_started(REAL_FAILURE_JOBS)


def test_an_unknown_argv_fails_the_test():
    with pytest.raises(AssertionError):
        FakeRunner({})(["gh", "pr", "merge", "1"])


def test_a_setup_only_failure_is_rerun_and_no_revert_port_is_called():
    runner = FakeRunner({B: [_run(101, B, "failure")]}, jobs={101: SETUP_ONLY_JOBS})

    actions = _plan(runner, [WATCH_B])

    assert [a["commit"] for a in _of(actions, "rerun_main")] == [B]
    assert _of(actions, "revert_land") == []

    calls: list = []
    results = _perform(_of(actions, "rerun_main"), runner, calls)

    assert runner.rerun_issued_for(101)
    assert [r["status"] for r in results if r["action"]["kind"] == "rerun_main"] == ["done"]
    assert _revert_calls(calls) == []


def test_a_real_failure_that_fails_again_after_the_one_rerun_is_reverted():
    rerun_row = _run(102, B, "failure", attempt=2)
    runner = FakeRunner(
        {B: [_run(101, B, "failure")]},
        jobs={101: REAL_FAILURE_JOBS},
        reruns={101: (rerun_row, REAL_FAILURE_JOBS)},
    )

    actions = _plan(runner, [WATCH_B])

    assert _of(actions, "rerun_main") == []
    [revert] = _of(actions, "revert_land")
    assert (revert["commit"], revert["pr"]) == (B, 8)

    _, _, run_id = chair_revert_ports.main_ci_run(runner, REPO, B)
    outcome = rerun_reds(
        [revert],
        {B: run_id},
        lambda repo, rid, commit: chair_revert_ports.rerun_failed(runner, repo, rid, commit),
        lambda repo, commit: chair_revert_ports.main_ci_run(runner, repo, commit),
        reran=(),
        failures=[],
        now=datetime(2026, 9, 25, 12, 0, tzinfo=UTC),
    )

    assert runner.rerun_issued_for(101)
    assert [r.state for r in outcome.recorded] == ["red"]
    [kept] = outcome.actions
    assert kept["kind"] == "revert_land"
    assert LOG in kept["reason"]

    calls: list = []
    results = _perform([kept], runner, calls)

    assert ("revert_onto_branch", REPO, "revert/8", B) in calls
    assert ("merge", PR_URL) in calls
    assert ("resolve", "ib", "p1", "reverted") in calls
    assert [r["status"] for r in results if r["action"]["kind"] == "revert_land"] == ["done"]


def test_a_walk_back_reverts_b_and_never_reverts_a():
    runner = FakeRunner(
        {
            A: [_run(100, A, "success")],
            B: [_run(101, B, "failure")],
            C: [_run(999, C, "failure")],  # newer than both lands, on a commit nobody watches
        },
        jobs={101: REAL_FAILURE_JOBS, 999: REAL_FAILURE_JOBS},
    )
    watches = [WATCH_A, WATCH_B]
    calls: list = []

    first = _plan(runner, watches)
    [revert] = _of(first, "revert_land")
    assert revert["commit"] == B
    _perform([revert], runner, calls, watches)

    assert watches == [WATCH_A]
    second = _plan(runner, watches)

    assert _of(second, "revert_land") == []
    assert _of(second, "rerun_main") == []
    reverted = [c for c in calls if c[0] == "revert_onto_branch"]
    assert reverted == [("revert_onto_branch", REPO, "revert/8", B)]
    assert all(A not in c and "revert/7" not in c for c in _revert_calls(calls))
    assert not any("999" in a or C in a for a in runner.argvs)


def test_a_land_older_than_a_green_main_run_plans_nothing():
    runner = FakeRunner(
        {A: [_run(100, A, "failure")], C: [_run(103, C, "success")]},
        jobs={100: REAL_FAILURE_JOBS},
        ancestry=frozenset({(A, C)}),
    )

    [landed] = _facts(runner, [WATCH_A])["landed_main"]
    actions = _plan(runner, [WATCH_A])

    assert (landed["ci"], landed["green_after"]) == ("red", True)
    assert _of(actions, "revert_land") == []
    assert _of(actions, "rerun_main") == []
