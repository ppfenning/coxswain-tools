from agent_tools.lane_hosts import LaneHost
from agent_tools.remote_argv import branch_push_argv, epic_refspec, ssh_argv, sync_argv
from agent_tools.remote_launch import Relaunch, launch_on_host, launch_plan

HOST = LaneHost("box", "me@box", "/ws")
HOME = "/".join(("", "home", "me"))
TILDE_REPO = "/".join(("~", "repos", "app"))
OTHER_REPO = "/".join(("", "home", "other", "repos", "app"))
RELATIVE = "/".join(("repos", "app"))
EXPANDED = "/".join((HOME, "repos", "app"))


def _plan(repo, relaunch=None, home=HOME):
    return launch_plan(HOST, "init", "run1", "lbl", repo=repo, relaunch=relaunch, home=home)


def test_a_tilde_repo_is_synced_by_its_home_relative_path():
    assert ssh_argv(HOST.ssh, sync_argv(RELATIVE)) in _plan(TILDE_REPO)


def test_a_relaunch_pushes_to_the_tilde_form_from_the_expanded_local_path():
    plan = _plan(TILDE_REPO, Relaunch("old"))
    assert branch_push_argv(EXPANDED, HOST.ssh, TILDE_REPO, epic_refspec("init")) in plan


def test_a_given_local_repo_wins_over_the_expanded_path():
    local = "/".join(("", "srv", "checkout"))
    plan = _plan(TILDE_REPO, Relaunch("old", local_repo=local))
    assert branch_push_argv(local, HOST.ssh, TILDE_REPO, epic_refspec("init")) in plan


def test_a_repo_under_another_users_home_maps_the_same_way():
    plan = _plan(OTHER_REPO, Relaunch("old"))
    assert ssh_argv(HOST.ssh, sync_argv(RELATIVE)) in plan
    assert branch_push_argv(EXPANDED, HOST.ssh, TILDE_REPO, epic_refspec("init")) in plan


def test_without_a_home_the_plan_is_unchanged_for_an_absolute_repo():
    repo = "/".join(("", "opt", "app"))
    plan = _plan(repo, Relaunch("old"), home=None)
    assert ssh_argv(HOST.ssh, sync_argv(repo)) in plan
    assert branch_push_argv(repo, HOST.ssh, repo, epic_refspec("init")) in plan
    assert plan == launch_plan(HOST, "init", "run1", "lbl", repo=repo, relaunch=Relaunch("old"))


def test_launch_on_host_runs_the_mapped_plan_and_records_the_repo_as_recorded():
    calls = []

    def run(argv):
        calls.append(argv)
        return 0

    record = launch_on_host(HOST, "init", "run1", "lbl", "t", run, repo=TILDE_REPO, relaunch=Relaunch("old"), home=HOME)
    assert calls == _plan(TILDE_REPO, Relaunch("old"))
    assert TILDE_REPO in str(record)
