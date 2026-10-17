from pathlib import Path

from agent_tools.upgrade_exec import newest_release, offered_tags, run_plan
from agent_tools.upgrade_plan import LeftBehind, Plan, Step

ROOT = Path("/r")
UMBRELLA = Step("umbrella", "umbrella", "v1", "v2")
CREW = Step("component", "crew", "v1", "v2")
DASH = Step("component", "dash", None, "v2")


def _recorder(fail_on=None):
    calls = []

    def run(argv, cwd):
        calls.append(argv)
        return (1, "boom") if fail_on and fail_on in argv else (0, "")

    return run, calls


def _git(directory, *args):
    return ["git", "-C", f"/r/{directory}", *args]


def test_umbrella_moves_before_crew_and_dash_and_each_gets_fetch_then_checkout():
    run, calls = _recorder()
    code = run_plan(Plan((UMBRELLA, CREW, DASH), ()), root=ROOT, run=run, dry_run=False)
    assert code == 0
    assert calls == [
        _git("coxswain", "fetch", "--tags"), _git("coxswain", "checkout", "v2"),
        _git("coxswain-crew", "fetch", "--tags"), _git("coxswain-crew", "checkout", "v2"),
        ["/r/coxswain-crew/install.sh"],
        _git("coxswain-dash", "fetch", "--tags"), _git("coxswain-dash", "checkout", "v2"),
    ]


def test_install_script_is_not_run_when_crew_has_no_step():
    run, calls = _recorder()
    run_plan(Plan((UMBRELLA, DASH), ()), root=ROOT, run=run, dry_run=False)
    assert ["/r/coxswain-crew/install.sh"] not in calls


def test_dry_run_prints_the_steps_and_makes_no_calls(capsys):
    run, calls = _recorder()
    code = run_plan(Plan((UMBRELLA, CREW), ()), root=ROOT, run=run, dry_run=True)
    assert (code, calls) == (0, [])
    assert "would move crew: v1 to v2" in capsys.readouterr().out


def test_left_behind_is_printed_and_exits_2(capsys):
    run, _ = _recorder()
    plan = Plan((UMBRELLA,), (LeftBehind("dash", "pin_not_in_repo", "pinned tag v9 is not offered"),))
    assert run_plan(plan, root=ROOT, run=run, dry_run=False) == 2
    assert "left behind: dash: pin_not_in_repo: pinned tag v9 is not offered" in capsys.readouterr().out


def test_a_failed_checkout_stops_the_plan_with_exit_2(capsys):
    run, calls = _recorder(fail_on="checkout")
    assert run_plan(Plan((UMBRELLA, CREW), ()), root=ROOT, run=run, dry_run=False) == 2
    assert calls == [_git("coxswain", "fetch", "--tags"), _git("coxswain", "checkout", "v2")]
    assert "FAILED: umbrella: boom" in capsys.readouterr().out


def test_a_component_with_a_clone_url_is_cloned_not_fetched():
    run, calls = _recorder()
    run_plan(Plan((DASH,), ()), root=ROOT, run=run, dry_run=False, clone_urls={"dash": "u"})
    assert calls == [["git", "clone", "u", "/r/coxswain-dash"], _git("coxswain-dash", "checkout", "v2")]


def test_newest_release_skips_prereleases():
    assert newest_release(["v3.1.0-rc1", "v3.0.0", "v2.9"]) == "v3.0.0"


def test_offered_tags_keeps_order_and_drops_peeled_duplicates():
    output = "a1\trefs/tags/v2\nb2\trefs/tags/v2^{}\nc3\trefs/tags/v1\nd4\trefs/heads/main\n"
    assert offered_tags(output) == ("v2", "v1")
