"""A task land that finds an open PR on its branch adopts it: wait, merge, clean, mark done."""
import pytest
from test_land import _record

from agent_tools import cli, land
from agent_tools.forge import ForgeError

_BRANCHES = {"agents/epic-x-5/seams-task": ["Add seams module"]}
_PLAN = land.land_plan(_record(), _BRANCHES, "main")
_ADOPT = {"kind": "adopt", "pr": 7}
_KINDS = ("wait_checks", "merge", "clean", "mark_done")


class FakeForge:
    def __init__(self, state="CLEAN", waits=((True, "green"),), update_fails=False):
        self.state, self.waits, self.update_fails = state, iter(waits), update_fails
        self.calls = []

    def merge_state(self, pr, *, repo=None):
        self.calls.append("merge_state")
        return self.state

    def update_branch(self, pr, *, repo=None):
        self.calls.append("update_branch")
        if self.update_fails:
            raise ForgeError("conflict")

    def wait_checks(self, repo, timeout_s, ref="HEAD"):
        self.calls.append("wait_checks")
        return next(self.waits)

    def merge(self, repo, step):
        self.calls.append("merge")
        return True, "merged"


@pytest.fixture
def walk(tmp_path, monkeypatch):
    """Walk the adopted steps; the local `clean` and `mark_done` are recorded, not run."""
    local = []
    monkeypatch.setattr(cli, "_fetch_branch", lambda repo, branch: None)
    monkeypatch.setattr(cli, "_mark_done_facts", lambda step, pr, now: step)
    real = cli._execute_land_step

    def execute(repo, step, forge=None):
        if step["kind"] in ("clean", "mark_done", "route_sync"):
            local.append(step["kind"])
            return True, "ok"
        return real(repo, step, forge)

    monkeypatch.setattr(cli, "_execute_land_step", execute)

    def run(forge):
        steps = land.resume_steps(_PLAN, _ADOPT, "pr/seams-task")
        rc, _, _ = cli._land_walk(tmp_path, steps, steps, None, None, "full", False, forge, [])
        return rc, forge.calls, local

    return run


def test_adopted_steps_reach_merge_then_mark_done(walk):
    rc, calls, local = walk(FakeForge())
    assert rc == 0
    assert calls == ["wait_checks", "merge_state", "merge"]
    assert local[:2] == ["clean", "mark_done"]


def test_no_open_pr_leaves_the_steps_unchanged():
    assert land.resume_steps(_PLAN, {"kind": "fresh"}, "pr/seams-task") == _PLAN


def test_adopted_steps_keep_the_order_without_the_pr_building_steps():
    kinds = [s["kind"] for s in land.resume_steps(_PLAN, _ADOPT, "pr/seams-task")]
    assert [k for k in kinds if k in _KINDS] == list(_KINDS)
    assert not {"cherry_pick", "checks", "push", "pr_create"} & set(kinds)


def test_a_failed_wait_checks_stops_before_merge_and_mark_done(walk, capsys):
    rc, calls, local = walk(FakeForge(waits=[(False, "check lint failed")]))
    assert rc == 1
    assert calls == ["wait_checks"] and local == []
    assert "wait_checks: check lint failed" in capsys.readouterr().out


def test_a_failing_update_branch_on_behind_stops_before_merge_and_mark_done(walk, capsys):
    rc, calls, local = walk(FakeForge("BEHIND", update_fails=True))
    assert rc == 1
    assert calls == ["wait_checks", "merge_state", "update_branch"] and local == []
    assert "cannot update pull request #7" in capsys.readouterr().out
