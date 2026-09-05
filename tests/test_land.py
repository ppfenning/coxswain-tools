import json
import os
import subprocess as sp
from pathlib import Path

import pytest

from agent_tools import cleanup, cli, land

_STEP_ORDER = ["pick_branch", "cherry_pick", "checks", "push", "pr_create", "wait_checks", "merge", "clean", "mark_done"]
_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}


def _record(**overrides):
    base = {
        "run": "epic-x-5",
        "task": "seams-task",
        "phase": "seams",
        "initiative": "x",
        "proposals": [{"kind": "draft_pr_create", "title": "Add seams"}],
        "review": {"verdict": "approve"},
        "arbitration": {"verdict": "approve"},
        "change_facts": {"fix_loop_attempts": 1, "checks": "pytest -q"},
        "build": {"summary": "Added the seams module."},
    }
    base.update(overrides)
    return base


# --- land_plan: pure ---

def test_refuse_without_approval():
    assert land.land_plan(_record(proposals=[]), {}, "main") == [{"kind": "refuse", "reason": "no draft_pr_create proposal in record"}]
    assert land.land_plan(_record(arbitration={"verdict": "revise"}), {}, "main") == [
        {"kind": "refuse", "reason": "arbitration verdict is 'revise', not 'approve'"}
    ]


def test_branch_choice_scratch_branch():
    branches = {"agents/epic-x-5/seams-task": ["Add seams module"], "epic/x/seams": ["Add seams module", "Add other thing"]}
    steps = land.land_plan(_record(), branches, "main")
    assert steps[0] == {"kind": "pick_branch", "branch": "agents/epic-x-5/seams-task", "commit_subject": "Add seams module"}


def test_branch_choice_phase_branch():
    branches = {"agents/epic-x-5/seams-task": ["c1", "c2"], "epic/x/seams": ["Add seams module"]}
    steps = land.land_plan(_record(), branches, "main")
    assert steps[0] == {"kind": "pick_branch", "branch": "epic/x/seams", "commit_subject": "Add seams module"}


def test_branch_choice_ambiguous_refuses_naming_what_was_found():
    branches = {"agents/epic-x-5/seams-task": ["c1", "c2"], "epic/x/seams": ["c1", "c2"]}
    steps = land.land_plan(_record(), branches, "main")
    assert steps == [{
        "kind": "refuse",
        "reason": "no branch is exactly one commit ahead of main",
        "found": {"agents/epic-x-5/seams-task": 2, "epic/x/seams": 2},
    }]


def test_step_order():
    branches = {"agents/epic-x-5/seams-task": ["Add seams module"]}
    steps = land.land_plan(_record(), branches, "main")
    assert [s["kind"] for s in steps] == _STEP_ORDER


def test_a_record_silent_on_initiative_still_lands_off_the_scratch_branch():
    branches = {"agents/epic-x-5/seams-task": ["Add seams module"]}
    steps = land.land_plan(_record(initiative=None), branches, "main")
    assert steps[0]["kind"] == "pick_branch"


def test_a_record_silent_on_initiative_refuses_cleanly_when_only_a_phase_branch_is_offered():
    branches = {"epic/x/seams": ["one commit"]}
    steps = land.land_plan(_record(initiative=None), branches, "main")
    assert steps == [{"kind": "refuse", "reason": "no branch is exactly one commit ahead of main", "found": {"epic/x/seams": 1}}]


# --- pr_body: pure ---

def test_pr_body_contains_verdicts_and_run_id():
    body = land.pr_body(_record())
    assert "epic-x-5" in body
    assert "Review verdict: approve" in body
    assert "Arbitration verdict: approve" in body


def test_pr_body_cost_line_survives_a_string_cost():
    assert "Cost: $1.50" in land.pr_body(_record(cost_usd="1.5"))


def test_pr_body_omits_the_cost_line_rather_than_raising_on_an_unparseable_cost():
    assert "Cost:" not in land.pr_body(_record(cost_usd="n/a"))


# --- cli._land_branches: the edge asks git, not prose, about merges ---

def test_land_branches_asks_git_for_no_merges_rather_than_sniffing_subjects(monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return sp.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    cli._land_branches(Path("/nonexistent"), _record(), "main")
    assert calls and all("--no-merges" in c for c in calls)


# --- cli._execute_land_step: the git-only arms, for real, no gh ---

@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"; root.mkdir()
    sp.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    (root / "f").write_text("x"); sp.run(["git", "-C", str(root), "add", "-A"], check=True, env=_ENV)
    sp.run(["git", "-C", str(root), "commit", "-qm", "init"], check=True, env=_ENV)
    sp.run(["git", "-C", str(root), "checkout", "-qb", "agents/epic-x-5/seams-task"], check=True, env=_ENV)
    (root / "f").write_text("y"); sp.run(["git", "-C", str(root), "commit", "-aqm", "Add seams module"], check=True, env=_ENV)
    sp.run(["git", "-C", str(root), "checkout", "-q", "main"], check=True, env=_ENV)
    return root


def test_execute_cherry_pick_lands_the_one_commit_on_a_fresh_branch(repo):
    ok, detail = cli._execute_land_step(repo, {
        "kind": "cherry_pick", "branch": "agents/epic-x-5/seams-task", "commit_subject": "Add seams module",
        "onto": "pr/seams-task", "from": "main",
    })
    assert ok, detail
    assert "pr/seams-task" in cleanup.git_branches(repo)
    assert (repo / "f").read_text() == "y"


def test_execute_cherry_pick_resolves_by_commit_range_not_by_subject_text(repo):
    # main already has an old commit with the same subject as the scratch
    # branch's one real commit; a --grep lookup could match the wrong one.
    sp.run(["git", "-C", repo, "commit", "--allow-empty", "-qm", "Add seams module"], check=True, env=_ENV)
    ok, detail = cli._execute_land_step(repo, {
        "kind": "cherry_pick", "branch": "agents/epic-x-5/seams-task", "commit_subject": "Add seams module",
        "onto": "pr/seams-task", "from": "main",
    })
    assert ok, detail
    assert (repo / "f").read_text() == "y"


def test_execute_cherry_pick_conflict_reports_failure_without_raising_and_leaves_the_repo_clean(repo):
    sp.run(["git", "-C", repo, "commit", "--allow-empty", "-qm", "noop"], check=True, env=_ENV)
    (repo / "f").write_text("conflicting"); sp.run(["git", "-C", repo, "commit", "-aqm", "unrelated main change"], check=True, env=_ENV)
    ok, detail = cli._execute_land_step(repo, {
        "kind": "cherry_pick", "branch": "agents/epic-x-5/seams-task", "commit_subject": "Add seams module",
        "onto": "pr/seams-task", "from": "main",
    })
    assert not ok
    assert detail
    status = sp.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout
    assert status.strip() == ""


def test_execute_checks_reports_pass_and_fail(tmp_path):
    assert cli._execute_land_step(tmp_path, {"kind": "checks", "command": "true"}) == (True, "true")
    ok, _ = cli._execute_land_step(tmp_path, {"kind": "checks", "command": "false"})
    assert not ok


def test_execute_push_reaches_a_real_remote(repo, tmp_path):
    origin = tmp_path / "origin.git"
    sp.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    sp.run(["git", "-C", str(repo), "remote", "add", "origin", str(origin)], check=True)
    ok, detail = cli._execute_land_step(repo, {"kind": "push", "branch": "main"})
    assert ok, detail
    show = sp.run(["git", "-C", str(origin), "show-ref", "refs/heads/main"], capture_output=True, text=True)
    assert show.returncode == 0


def test_execute_clean_uses_the_given_worktree_root_not_a_hardcoded_one(tmp_path):
    root = tmp_path / "r"; root.mkdir()
    sp.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    (root / "f").write_text("x"); sp.run(["git", "-C", str(root), "add", "-A"], check=True, env=_ENV)
    sp.run(["git", "-C", str(root), "commit", "-qm", "init"], check=True, env=_ENV)
    sp.run(["git", "-C", str(root), "branch", "epic/x/seams"], check=True)
    sp.run(["git", "-C", str(root), "branch", "agents/epic-x-5/seams-task"], check=True)
    wt = tmp_path / "wt/epic-x-5/seams"; wt.parent.mkdir(parents=True)
    sp.run(["git", "-C", str(root), "worktree", "add", "-q", str(wt), "agents/epic-x-5/seams-task"], check=True)
    ok, detail = cli._execute_land_step(root, {"kind": "clean", "run": "epic-x-5", "worktree_root": str(tmp_path / "wt")})
    assert ok, detail
    assert "agents/epic-x-5/seams-task" not in cleanup.git_branches(root)
    assert "epic/x/seams" in cleanup.git_branches(root)
    assert not wt.exists()


def test_execute_mark_done_writes_landed_true_to_the_record(tmp_path):
    path = tmp_path / "task.json"
    path.write_text(json.dumps(_record()), encoding="utf-8")
    ok, detail = cli._execute_land_step(tmp_path, {"kind": "mark_done", "task": "seams-task", "path": str(path)})
    assert ok, detail
    assert json.loads(path.read_text())["landed"] is True


# --- cli end to end: dry-run default, and the dirty-checkout refusal ---

def test_cli_dry_run_is_the_default_and_prints_the_plan(repo, tmp_path, capsys, monkeypatch):
    task_dir = tmp_path / "runs/epic-x-5/tasks/seams"; task_dir.mkdir(parents=True)
    (task_dir / "seams-task.json").write_text(json.dumps(_record()), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["runs", "land", "epic-x-5", "--repo", str(repo)])
    out = capsys.readouterr().out
    assert rc == 0
    assert '"kind": "pick_branch"' in out
    assert "agents/epic-x-5/seams-task" in out
    assert all(kind in out for kind in _STEP_ORDER)


def test_cli_apply_refuses_on_a_dirty_checkout(repo, tmp_path, capsys, monkeypatch):
    task_dir = tmp_path / "runs/epic-x-5/tasks/seams"; task_dir.mkdir(parents=True)
    (task_dir / "seams-task.json").write_text(json.dumps(_record()), encoding="utf-8")
    (repo / "f").write_text("dirty")
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["runs", "land", "epic-x-5", "--repo", str(repo), "--apply"])
    assert rc == 2
    assert "dirty" in capsys.readouterr().out


def test_cli_apply_refuses_when_the_pr_branch_already_exists(repo, tmp_path, capsys, monkeypatch):
    task_dir = tmp_path / "runs/epic-x-5/tasks/seams"; task_dir.mkdir(parents=True)
    (task_dir / "seams-task.json").write_text(json.dumps(_record()), encoding="utf-8")
    sp.run(["git", "-C", str(repo), "branch", "pr/seams-task"], check=True, capture_output=True)
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["runs", "land", "epic-x-5", "--repo", str(repo), "--apply"])
    assert rc == 2
    assert "already exists" in capsys.readouterr().out
