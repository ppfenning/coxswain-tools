from datetime import UTC, datetime

from agent_tools import chair_exec, chair_report
from agent_tools.chair_run import RunDeps, tick

NOW = datetime(2026, 9, 26, 18, 5, tzinfo=UTC)
LEASE = {"holder": "me", "host": "box", "epoch": 3, "mine": False, "released": True, "stale": False}
FACTS = {
    "lease": LEASE,
    "limits": {"hard_stop": False, "weekly_fraction": 0.1, "hard_stop_fraction": 0.9, "launch_cap": 2, "go_degraded": False},
    "dispatch": {"max_in_flight": 4, "live_runs": 0, "hosts": []},
    "approved": [], "initiatives": [], "quarantines": [], "intake": [],
    "work_store_ready": False, "sources_configured": False,
    "last_housekeeping_at": NOW.isoformat(), "housekeeping_hours": 24,
}


class Stubs:
    """Every dep as a stub; `log` holds the order the tick called them in, `recorded` what reached the store."""

    def __init__(self, reasons=()):
        self.log, self.recorded, self.reasons = [], [], list(reasons)

    def _pull(self):
        self.log.append("pull")
        return self.reasons.pop(0) if self.reasons else ""

    def _perform(self, actions, exec_deps, current_epoch, dry_run):
        self.log.append("perform")
        return chair_exec.perform(actions, exec_deps, current_epoch, dry_run)

    def deps(self, pull=True) -> RunDeps:
        exec_deps = chair_exec.Deps(
            run=lambda argv: (0, ""),
            delete_branches=lambda repo, pattern: ([], ""),
            acquire_lease=lambda holder, host: self.log.append("take") or "",
            record=self.recorded.append,
            run_id=lambda action: "run-1",
            repo_for=lambda action: "r",
        )
        return RunDeps(
            facts_deps=object(),
            exec_deps=exec_deps,
            report_deps=chair_report.Deps(echo=lambda line: None, notify=None),
            beat=lambda: self.log.append("beat"),
            current_epoch=lambda: 3,
            holds=lambda: True,
            release=lambda: None,
            sleep=lambda seconds: None,
            now=lambda: NOW,
            gather=lambda _deps, _now: self.log.append("gather") or FACTS,
            plan=lambda facts, now: [{"kind": "take_lease"}],
            perform=self._perform,
            pull_workspace=self._pull if pull else None,
        )

    def pulled(self):
        return [a for a in self.recorded if a["kind"] == "pull_workspace"]


def test_a_take_lease_tick_pulls_after_the_beat_and_before_gather_and_perform():
    stubs = Stubs()
    tick(stubs.deps(), False, NOW)
    assert stubs.log[:4] == ["beat", "pull", "gather", "perform"]
    assert stubs.log.index("pull") < stubs.log.index("take")
    assert stubs.pulled() == []


def test_a_failed_pull_still_takes_the_lease_and_shows_in_the_line_and_one_action():
    stubs = Stubs(["dirty tree"])
    line = tick(stubs.deps(), False, NOW)
    assert "take" in stubs.log
    assert "workspace pull failed: dirty tree" in line
    assert [a["reason"] for a in stubs.pulled()] == ["dirty tree"]


def test_the_same_reason_is_recorded_once_and_a_new_reason_is_recorded_again():
    stubs = Stubs(["dirty tree", "dirty tree", "diverged"])
    deps = stubs.deps()
    lines = [tick(deps, False, NOW) for _ in range(3)]
    assert all("workspace pull failed:" in line for line in lines)
    assert [a["reason"] for a in stubs.pulled()] == ["dirty tree", "diverged"]


def test_a_dry_run_never_pulls():
    stubs = Stubs(["dirty tree"])
    line = tick(stubs.deps(), True, NOW)
    assert "pull" not in stubs.log
    assert "workspace pull failed" not in line


def test_no_pull_workspace_changes_nothing():
    stubs = Stubs()
    line = tick(stubs.deps(pull=False), False, NOW)
    assert "pull" not in stubs.log and "workspace pull failed" not in line
    assert stubs.pulled() == []


def test_a_pull_that_raises_is_a_failure_reason_and_the_tick_goes_on():
    stubs = Stubs()
    stubs._pull = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    line = tick(stubs.deps(), False, NOW)
    assert "take" in stubs.log and "workspace pull failed: boom" in line
