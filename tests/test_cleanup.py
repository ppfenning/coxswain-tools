import json
import os
import subprocess as sp

import pytest

from agent_tools import cleanup, cli


def test_plan_keeps_phase_branches_and_targets_only_this_run(tmp_path):
    plan = cleanup.plan_cleanup(
        run_id="epic-x-5",
        worktrees=["/repo", str(tmp_path / "wt/epic-x-5/seams"), str(tmp_path / "wt/epic-x-5/seams/task"), str(tmp_path / "wt/epic-x-4/seams")],
        branches=["main", "epic/x/seams", "epic/x/seams--task", "agents/epic-x-5/task", "agents/epic-x-4/other"],
        worktree_root=str(tmp_path / "wt"),
    )
    assert plan["worktrees"] == [str(tmp_path / "wt/epic-x-5/seams"), str(tmp_path / "wt/epic-x-5/seams/task")]
    assert plan["branches"] == ["epic/x/seams--task", "agents/epic-x-5/task"]
    assert plan["kept_phase_branches"] == ["epic/x/seams"]


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "r"; root.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
    sp.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    (root / "f").write_text("x"); sp.run(["git", "-C", str(root), "add", "-A"], check=True, env=env)
    sp.run(["git", "-C", str(root), "commit", "-qm", "init"], check=True, env=env)
    sp.run(["git", "-C", str(root), "branch", "epic/x/seams"], check=True)
    sp.run(["git", "-C", str(root), "branch", "agents/epic-x-5/task"], check=True)
    sp.run(["git", "-C", str(root), "branch", "epic/x/seams--task2"], check=True)
    wt = tmp_path / "wt/epic-x-5/seams"; wt.parent.mkdir(parents=True)
    sp.run(["git", "-C", str(root), "worktree", "add", "-q", str(wt), "agents/epic-x-5/task"], check=True)
    return root, tmp_path / "wt"


def test_apply_is_a_dry_run_unless_asked(repo):
    root, wtroot = repo
    plan = cleanup.plan_cleanup(run_id="epic-x-5", worktrees=cleanup.git_worktrees(root), branches=cleanup.git_branches(root), worktree_root=str(wtroot))
    lines = cleanup.apply_cleanup(root, plan)
    assert any(l.startswith("would remove worktree") for l in lines) and "agents/epic-x-5/task" in cleanup.git_branches(root)
    lines = cleanup.apply_cleanup(root, plan, dry_run=False)
    assert any(l.startswith("removed worktree") for l in lines)
    assert "agents/epic-x-5/task" not in cleanup.git_branches(root) and "epic/x/seams" in cleanup.git_branches(root)
    assert not (wtroot / "epic-x-5").exists()


def test_a_branch_already_on_main_is_deleted_without_a_landed_record(repo):
    root, wtroot = repo
    plan = cleanup.plan_cleanup(run_id="epic-x-5", worktrees=cleanup.git_worktrees(root), branches=cleanup.git_branches(root), worktree_root=str(wtroot))
    lines = cleanup.apply_cleanup(root, plan, dry_run=False)
    assert any(l == "deleted branch agents/epic-x-5/task" for l in lines)
    assert any(l == "deleted branch epic/x/seams--task2" for l in lines)
    assert "agents/epic-x-5/task" not in cleanup.git_branches(root)
    assert "epic/x/seams--task2" not in cleanup.git_branches(root)


@pytest.fixture
def repo_unlanded(tmp_path):
    root = tmp_path / "r"; root.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
    sp.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    (root / "f").write_text("x"); sp.run(["git", "-C", str(root), "add", "-A"], check=True, env=env)
    sp.run(["git", "-C", str(root), "commit", "-qm", "init"], check=True, env=env)
    sp.run(["git", "-C", str(root), "checkout", "-qb", "agents/epic-x-5/task"], check=True, env=env)
    (root / "g").write_text("y"); sp.run(["git", "-C", str(root), "add", "-A"], check=True, env=env)
    sp.run(["git", "-C", str(root), "commit", "-qm", "task work"], check=True, env=env)
    sp.run(["git", "-C", str(root), "checkout", "-q", "main"], check=True, env=env)
    return root


def test_an_unlanded_branch_with_a_commit_not_on_main_is_kept(repo_unlanded):
    root = repo_unlanded
    plan = cleanup.plan_cleanup(run_id="epic-x-5", worktrees=[], branches=cleanup.git_branches(root), worktree_root=str(root.parent / "wt"))
    lines = cleanup.apply_cleanup(root, plan, dry_run=False)
    assert any(l.startswith("kept agents/epic-x-5/task: approved, not on main") for l in lines)
    assert "agents/epic-x-5/task" in cleanup.git_branches(root)


def test_a_landed_task_record_lets_the_branch_be_deleted(repo_unlanded):
    root = repo_unlanded
    plan = cleanup.plan_cleanup(run_id="epic-x-5", worktrees=[], branches=cleanup.git_branches(root), worktree_root=str(root.parent / "wt"))
    lines = cleanup.apply_cleanup(root, plan, dry_run=False, landed=["task"])
    assert any(l == "deleted branch agents/epic-x-5/task" for l in lines)
    assert "agents/epic-x-5/task" not in cleanup.git_branches(root)


def test_force_deletes_the_unlanded_branch_and_says_so(repo_unlanded):
    root = repo_unlanded
    plan = cleanup.plan_cleanup(run_id="epic-x-5", worktrees=[], branches=cleanup.git_branches(root), worktree_root=str(root.parent / "wt"))
    lines = cleanup.apply_cleanup(root, plan, dry_run=False, force=True)
    assert any(l == "deleted branch agents/epic-x-5/task (forced)" for l in lines)
    assert "agents/epic-x-5/task" not in cleanup.git_branches(root)


def test_dry_run_prints_the_kept_line_and_deletes_nothing(repo_unlanded):
    root = repo_unlanded
    plan = cleanup.plan_cleanup(run_id="epic-x-5", worktrees=[], branches=cleanup.git_branches(root), worktree_root=str(root.parent / "wt"))
    lines = cleanup.apply_cleanup(root, plan)
    assert any(l.startswith("kept agents/epic-x-5/task: approved, not on main") for l in lines)
    assert "agents/epic-x-5/task" in cleanup.git_branches(root)


@pytest.fixture
def repo_epic_unlanded(tmp_path):
    root = tmp_path / "r"; root.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
    sp.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    (root / "f").write_text("x"); sp.run(["git", "-C", str(root), "add", "-A"], check=True, env=env)
    sp.run(["git", "-C", str(root), "commit", "-qm", "init"], check=True, env=env)
    sp.run(["git", "-C", str(root), "checkout", "-qb", "epic/x/seams--task2"], check=True, env=env)
    (root / "g").write_text("y"); sp.run(["git", "-C", str(root), "add", "-A"], check=True, env=env)
    sp.run(["git", "-C", str(root), "commit", "-qm", "task work"], check=True, env=env)
    sp.run(["git", "-C", str(root), "checkout", "-q", "main"], check=True, env=env)
    return root


def test_an_unlanded_epic_task_branch_with_a_commit_not_on_main_is_kept(repo_epic_unlanded):
    root = repo_epic_unlanded
    plan = cleanup.plan_cleanup(run_id="epic-x-5", worktrees=[], branches=cleanup.git_branches(root), worktree_root=str(root.parent / "wt"))
    lines = cleanup.apply_cleanup(root, plan, dry_run=False)
    assert any(l.startswith("kept epic/x/seams--task2: approved, not on main") for l in lines)
    assert "epic/x/seams--task2" in cleanup.git_branches(root)


def test_a_landed_epic_task_branch_is_deleted(repo_epic_unlanded):
    root = repo_epic_unlanded
    plan = cleanup.plan_cleanup(run_id="epic-x-5", worktrees=[], branches=cleanup.git_branches(root), worktree_root=str(root.parent / "wt"))
    lines = cleanup.apply_cleanup(root, plan, dry_run=False, landed=["task2"])
    assert any(l == "deleted branch epic/x/seams--task2" for l in lines)
    assert "epic/x/seams--task2" not in cleanup.git_branches(root)


def test_cox_runs_clean_reads_the_landed_record_through_the_profiles_workspace_dir(repo_unlanded, tmp_path, capsys):
    task_dir = tmp_path / "workspace/runs/epic-x-5/tasks/seams"; task_dir.mkdir(parents=True)
    (task_dir / "task.json").write_text(json.dumps({"landed": True}), encoding="utf-8")
    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(f"workspace_dir: {tmp_path / 'workspace'}\n", encoding="utf-8")
    rc = cli.main(["runs", "clean", "epic-x-5", "--repo", str(repo_unlanded), "--profile", str(profile_path), "--apply"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "deleted branch agents/epic-x-5/task" in out
    assert "agents/epic-x-5/task" not in cleanup.git_branches(repo_unlanded)
