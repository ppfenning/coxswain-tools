"""A leftover pr branch is decided from facts alone, before any cherry-picked tree exists."""

from agent_tools import land

BRANCH = "pr/p--ph1"


def decide(**facts):
    return land.leftover_branch_decision(BRANCH, facts.pop("remote_exists", False), False, "t0", None,
                                         local_commit="c1", **facts)


def test_ancestor_of_main_with_no_remote_and_no_pr_recreates_without_a_cherry_picked_tree():
    assert decide(is_ancestor_of_main=True, unique_commits=0) == {"kind": "recreate", "old_tip": "c1"}


def test_unique_commits_refuse_and_name_the_count_and_the_branch():
    assert decide(is_ancestor_of_main=False, unique_commits=3) == {
        "kind": "refuse", "reason": f"{BRANCH} has 3 commits not on main"}


def test_open_pr_refuses_and_names_the_pr_number():
    assert decide(is_ancestor_of_main=True, unique_commits=0, open_pr_number=42) == {
        "kind": "refuse", "reason": f"{BRANCH} has open pull request #42"}


def test_open_pr_wins_over_unique_commits():
    assert decide(unique_commits=2, open_pr_number=42) == {
        "kind": "refuse", "reason": f"{BRANCH} has open pull request #42"}


def test_a_remote_branch_keeps_today_s_back_up_and_rebuild():
    assert decide(remote_exists=True, is_ancestor_of_main=True, unique_commits=0, remote_commit="r1") == {
        "kind": "back_up_and_rebuild", "backup_ref": f"refs/backup/{BRANCH}", "old_commit": "c1",
        "local_tip": "c1", "remote_tip": "r1",
        "backup_refs": {f"refs/backup/{BRANCH}": "c1", f"refs/backup/origin/{BRANCH}": "r1"}}


def test_phase_resume_refuses_a_local_branch_with_unique_commits():
    facts = {"local_tip": "a", "remote_tip": None, "pr": None, "pr_head": None, "unique_commits": 2}
    assert land.phase_resume(facts, BRANCH) == {"kind": "refuse", "reason": f"{BRANCH} has 2 commits not on main"}
