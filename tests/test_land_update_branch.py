"""A land reads the PR's merge state before `merge` and updates a BEHIND branch first."""
import functools

from agent_tools import cli, land, store_cli


class FakeForge:
    def __init__(self, state, waits=((True, "green"),), merges=None):
        self.state = state
        self.waits = iter(waits)
        self.merges = None if merges is None else iter(merges)
        self.calls = []
        self.repos = []

    def merge_state(self, pr, *, repo=None):
        self.calls.append("merge_state")
        self.repos.append(repo)
        return self.state

    def update_branch(self, pr, *, repo=None):
        self.calls.append("update_branch")
        self.repos.append(repo)

    def wait_checks(self, repo, timeout_s, ref="HEAD"):
        self.calls.append("wait_checks")
        return next(self.waits)

    def merge(self, repo, step):
        self.calls.append("merge")
        return (True, "merged") if self.merges is None else next(self.merges)


STEPS = [{"kind": "wait_checks", "branch": "origin/pr/x", "pr": 7}, {"kind": "merge", "branch": "pr/x"}]


def _walk(tmp_path, monkeypatch, forge):
    monkeypatch.setattr(cli, "_fetch_branch", lambda repo, branch: None)
    return cli._land_walk(tmp_path, STEPS, STEPS, None, None, "full", False, forge, [])


def test_behind_updates_waits_again_then_merges(tmp_path, monkeypatch):
    forge = FakeForge("BEHIND", [(True, "green"), (True, "green")])
    rc, _, _ = _walk(tmp_path, monkeypatch, forge)
    assert rc == 0
    assert forge.calls == ["wait_checks", "merge_state", "update_branch", "wait_checks", "merge"]
    assert forge.repos == [tmp_path, tmp_path]  # gh runs in the land's repo, never the chair's working directory


def test_behind_with_a_failing_rewait_stops_before_merge(tmp_path, monkeypatch, capsys):
    forge = FakeForge("BEHIND", [(True, "green"), (False, "check lint failed")])
    rc, _, _ = _walk(tmp_path, monkeypatch, forge)
    assert rc == 1
    assert "merge" not in forge.calls
    assert "wait_checks: check lint failed" in capsys.readouterr().out


def test_clean_merges_with_no_update_and_no_extra_wait(tmp_path, monkeypatch):
    forge = FakeForge("CLEAN")
    rc, _, _ = _walk(tmp_path, monkeypatch, forge)
    assert rc == 0
    assert forge.calls == ["wait_checks", "merge_state", "merge"]


def test_dirty_stops_naming_the_state(tmp_path, monkeypatch, capsys):
    forge = FakeForge("DIRTY")
    rc, _, _ = _walk(tmp_path, monkeypatch, forge)
    assert rc == 1
    assert "DIRTY" in capsys.readouterr().out
    assert "update_branch" not in forge.calls
    assert "merge" not in forge.calls


def test_merge_gate_clean_merges():
    assert cli.merge_gate("CLEAN") == ("merge", "")


def test_merge_gate_behind_updates_then_merges():
    assert cli.merge_gate("BEHIND") == ("update_then_merge", "")


def test_merge_gate_stops_only_on_a_state_that_can_never_merge():
    for state in ("DIRTY", "BLOCKED", "DRAFT"):
        assert cli.merge_gate(state) == ("stop", state)
    for state in ("UNSTABLE", "HAS_HOOKS", "UNKNOWN"):
        assert cli.merge_gate(state) == ("merge", "")


def test_settled_merge_state_rereads_unknown_until_it_settles():
    reads, naps = iter(["UNKNOWN", "UNKNOWN", "CLEAN"]), []
    assert cli._settled_merge_state(lambda: next(reads), naps.append) == "CLEAN"
    assert naps == [cli._UNKNOWN_WAIT_S, cli._UNKNOWN_WAIT_S]


def test_settled_merge_state_gives_up_as_unknown_after_its_reads():
    naps = []
    assert cli._settled_merge_state(lambda: "UNKNOWN", naps.append) == "UNKNOWN"
    assert len(naps) == cli._UNKNOWN_READS - 1


def test_unknown_that_settles_clean_merges(tmp_path, monkeypatch):
    forge = FakeForge("UNKNOWN")
    states = iter(["UNKNOWN", "CLEAN"])
    forge.merge_state = lambda pr, *, repo=None: (forge.calls.append("merge_state"), next(states))[1]
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    rc, _, _ = _walk(tmp_path, monkeypatch, forge)
    assert rc == 0
    assert forge.calls == ["wait_checks", "merge_state", "merge_state", "merge"]


def test_pr_number_prefers_the_wait_checks_pr_then_the_url():
    assert cli._pr_number(STEPS, 1, "") == 7
    assert cli._pr_number([{"kind": "merge"}], 0, "https://github.com/o/r/pull/12") == 12
    assert cli._pr_number([{"kind": "merge"}], 0, "") is None


BEHIND_REFUSAL = "Pull request is not mergeable: the head branch is not up to date with the base branch"


class FakeLease:
    """A repo lease store that grants every renewal but the ones scripted to be refused, and counts them."""

    def __init__(self, refuse_on=()):
        self.refuse_on = set(refuse_on)
        self.renewals = 0

    def renew(self):
        self.renewals += 1
        if self.renewals in self.refuse_on:
            return store_cli.LeaseRefused(epoch=4, holder="other@host:99")
        return store_cli.LeaseGranted(epoch=3, holder="me@host:1")


def _leased_walk(tmp_path, monkeypatch, forge, lease, attempts=None):
    monkeypatch.setattr(cli, "_fetch_branch", lambda repo, branch: None)
    monkeypatch.setattr(cli, "_merge_updating", functools.partial(cli._merge_updating, attempts=attempts))
    guard = cli._repo_lease_guard(str(tmp_path), lease.renew, lambda: 0.0, 1200, [])
    return cli._land_walk(tmp_path, STEPS, STEPS, None, None, "full", False, forge, [], guard=guard)


def test_merge_refused_as_behind_updates_waits_again_then_merges(tmp_path, monkeypatch):
    forge = FakeForge("CLEAN", [(True, "green"), (True, "green")], merges=[(False, BEHIND_REFUSAL), (True, "merged")])
    rc, _, _ = _leased_walk(tmp_path, monkeypatch, forge, FakeLease())
    assert rc == 0
    assert forge.calls == ["wait_checks", "merge_state", "merge", "update_branch", "wait_checks", "merge"]


def test_behind_past_the_bound_stops_once_at_the_merge(tmp_path, monkeypatch, capsys):
    forge = FakeForge("CLEAN", [(True, "green")] * 3, merges=[(False, BEHIND_REFUSAL)] * 3)
    rc, _, _ = _leased_walk(tmp_path, monkeypatch, forge, FakeLease(), attempts=2)
    out = capsys.readouterr().out
    reason = land.behind_stop(2, BEHIND_REFUSAL)
    assert rc == 1
    assert forge.calls.count("update_branch") == 2
    assert f"land: stopped at merge: {reason}" in out
    assert out.count(reason) == 1
    assert out.count(BEHIND_REFUSAL) == 1
    assert "after 2 branch updates" in reason


def test_non_behind_merge_failure_does_not_update(tmp_path, monkeypatch, capsys):
    forge = FakeForge("CLEAN", merges=[(False, "required reviews are missing")])
    rc, _, _ = _leased_walk(tmp_path, monkeypatch, forge, FakeLease())
    assert rc == 1
    assert "update_branch" not in forge.calls
    assert "land: stopped at merge: required reviews are missing" in capsys.readouterr().out


def test_lease_is_renewed_once_per_pass(tmp_path, monkeypatch):
    forge = FakeForge("CLEAN", [(True, "green")] * 3, merges=[(False, BEHIND_REFUSAL)] * 2 + [(True, "merged")])
    lease = FakeLease()
    rc, _, _ = _leased_walk(tmp_path, monkeypatch, forge, lease)
    assert rc == 0
    assert forge.calls.count("update_branch") == 2
    assert lease.renewals == 2 + 2  # the walk's guard renews before wait_checks and merge, the loop before each update


def test_lease_lost_at_the_top_of_a_pass_stops_before_the_update(tmp_path, monkeypatch, capsys):
    forge = FakeForge("CLEAN", merges=[(False, BEHIND_REFUSAL)])
    rc, _, _ = _leased_walk(tmp_path, monkeypatch, forge, FakeLease(refuse_on=[3]))
    assert rc == 1
    assert "update_branch" not in forge.calls
    assert "other@host:99" in capsys.readouterr().out


def test_is_behind_refusal_classifies_by_message():
    assert land.is_behind_refusal(BEHIND_REFUSAL) is True
    assert land.is_behind_refusal("required reviews are missing") is False
