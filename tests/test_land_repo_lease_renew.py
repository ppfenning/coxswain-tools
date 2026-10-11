import pytest

from agent_tools import cli, store_cli

REPO = "/r/repo"
TTL = 1200
STEPS = [{"kind": "push", "branch": "pr/x"}, {"kind": "pr_create"}, {"kind": "wait_checks"}]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _walk(monkeypatch, tmp_path, guard):
    ran = []
    monkeypatch.setattr(cli, "_execute_land_step", lambda repo, step, forge=None: (ran.append(step["kind"]), (True, "ok"))[1])
    rc, reached, _ = cli._land_walk(tmp_path, STEPS, STEPS, None, None, "merge", False, None, [], guard=guard)
    return rc, reached, ran


def test_a_clock_past_the_ttl_renews_before_every_step_and_the_walk_finishes(monkeypatch, tmp_path):
    clock, renewed, lost = Clock(), [], []

    def renew():
        renewed.append(clock.now)
        clock.now += TTL + 1
        return store_cli.LeaseGranted(7, "me")

    rc, reached, ran = _walk(monkeypatch, tmp_path, cli._repo_lease_guard(REPO, renew, clock, TTL, lost))
    assert (rc, reached, ran, lost) == (0, ["push", "pr_create", "wait_checks"], ["push", "pr_create", "wait_checks"], [])
    assert renewed == [0.0, TTL + 1, 2 * (TTL + 1)]


def test_a_refused_renew_stops_the_walk_at_that_step_and_names_the_repo(monkeypatch, tmp_path, capsys):
    clock, lost, answers = Clock(), [], iter([store_cli.LeaseGranted(7, "me"), store_cli.LeaseRefused(8, "other@h:9")])
    guard = cli._repo_lease_guard(REPO, lambda: next(answers), clock, TTL, lost)
    rc, reached, ran = _walk(monkeypatch, tmp_path, guard)
    out = capsys.readouterr().out
    assert (rc, reached, ran) == (2, ["push"], ["push"])
    assert "land: refusing pr_create, the repo lease for /r/repo was lost: held by other@h:9 at epoch 8" in out
    assert "stopped; remaining: pr_create, wait_checks" in out
    assert len(lost) == 1


@pytest.mark.parametrize("result, why", [
    (store_cli.LeaseError("boom"), "boom"),
    (store_cli.NotAvailable(), "the store is not available"),
    (store_cli.NotAvailable("no harness"), "no harness"),
])
def test_an_unrenewable_lease_stops_the_walk_but_is_not_marked_lost(monkeypatch, tmp_path, capsys, result, why):
    lost = []
    rc, reached, ran = _walk(monkeypatch, tmp_path, cli._repo_lease_guard(REPO, lambda: result, Clock(), TTL, lost))
    assert (rc, reached, ran, lost) == (2, [], [], [])
    assert f"land: refusing push, the repo lease for /r/repo could not be renewed: {why}" in capsys.readouterr().out


def test_the_stop_says_how_long_the_lease_went_unrenewed_when_it_had_expired():
    refused = store_cli.LeaseRefused(8, "other")
    assert "1500s since the last renewal against a 1200s ttl" in cli._repo_lease_stop(REPO, "merge", refused, 0.0, 1500.0, TTL)
    assert "since the last renewal" not in cli._repo_lease_stop(REPO, "merge", refused, 0.0, 10.0, TTL)
    assert cli._repo_lease_stop(REPO, "merge", store_cli.LeaseGranted(7, "me"), 0.0, 9999.0, TTL) is None


def test_the_second_guard_runs_only_when_the_first_lets_the_step_run():
    seen = []

    def then(kind):
        seen.append(kind)

    assert cli._first_stop(lambda kind: None, then)("merge") is None
    assert cli._first_stop(lambda kind: "lost", then)("push") == "lost"
    assert cli._first_stop(lambda kind: None, None)("push") is None
    assert seen == ["merge"]
