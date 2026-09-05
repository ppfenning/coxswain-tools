import os
import subprocess as sp

import pytest

from agent_tools import cleanup


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
