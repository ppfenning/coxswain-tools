"""`.agent-generate` in the land worktree: run, amend, and a resume check that agrees with the pushed tree."""

import subprocess as sp
import types

import pytest

from agent_tools import cli, forge_github, route

STEP = {"kind": "cherry_pick", "branch": "agents/x", "commit_subject": "Add x", "onto": "pr/x", "from": "main"}
NO_OPEN_PRS = types.SimpleNamespace(find_open_prs=lambda _repo, _branch: [])


def _git(repo, *args):
    return sp.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(autouse=True)
def _own_tempdir(tmp_path, monkeypatch):
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path / "tmp"))


def _repo_generating(tmp_path, command):
    """A checkout with a local bare `origin`, an ignored `.venv` the land links into its worktree, and an `agents/x`
    branch one commit ahead of main whose `.agent-generate` holds a comment and `command`."""
    bare, root = tmp_path / "origin.git", tmp_path / "repo"
    sp.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    sp.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    for args in (["config", "user.email", "t@e"], ["config", "user.name", "t"], ["remote", "add", "origin", str(bare)]):
        _git(root, *args)
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".gitignore").write_text(".venv/\n")
    (root / "f").write_text("x")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    _git(root, "push", "-q", "origin", "main")
    _git(root, "checkout", "-qb", "agents/x")
    (root / "f").write_text("y")
    (root / ".agent-generate").write_text(f"# regenerate\n{command}\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "Add x")
    _git(root, "checkout", "-q", "main")
    return root


def test_a_generated_file_lands_in_the_amended_commit(tmp_path, capsys):
    repo = _repo_generating(tmp_path, "echo generated > generated.txt")
    ok, detail = cli._execute_land_step(repo, STEP)
    assert ok, detail
    assert _git(repo, "show", "--name-only", "--format=", "pr/x").split() == [".agent-generate", "f", "generated.txt"]
    assert _git(repo, "rev-list", "--count", "main..pr/x") == "1"
    assert "land: regenerated generated.txt\n" in capsys.readouterr().out


def test_a_command_that_changes_nothing_prints_nothing_and_does_not_amend(tmp_path, capsys):
    repo = _repo_generating(tmp_path, "true")
    ok, detail = cli._execute_land_step(repo, STEP)
    assert ok, detail
    assert "regenerated" not in capsys.readouterr().out
    assert "amend" not in _git(repo, "reflog", "show", "pr/x")
    assert _git(repo, "show", "--name-only", "--format=", "pr/x").split() == [".agent-generate", "f"]


def test_a_command_naming_an_unset_umbrella_is_skipped_with_a_note(tmp_path, capsys):
    repo = _repo_generating(tmp_path, "echo $COX_UMBRELLA > u.txt")
    ok, detail = cli._execute_land_step(repo, STEP)
    assert ok, detail
    assert "land: skipped 'echo $COX_UMBRELLA > u.txt': umbrella_dir is unset" in capsys.readouterr().out
    assert "u.txt" not in _git(repo, "show", "--name-only", "--format=", "pr/x")


def test_a_failing_command_stops_the_land_before_any_push(tmp_path, capsys):
    repo = _repo_generating(tmp_path, "echo boom >&2; exit 3")
    rc, reached, _ = cli._land_execute(repo, [STEP, {"kind": "push", "branch": "pr/x"}], [], None, None, "auto", False, forge_github)
    out = capsys.readouterr().out
    wt = cli._land_worktree(repo, "pr/x")
    assert (rc, reached) == (1, ["cherry_pick"])
    assert "cherry_pick: generate failed: 'echo boom >&2; exit 3' exited 3: boom\n" in out
    assert f"land: worktree left at {wt} for inspection\n" in out
    assert "stopped; remaining: push" in out
    assert (wt / ".agent-generate").is_file()
    assert _git(repo, "ls-remote", "--heads", "origin", "pr/x") == ""


def test_a_failed_generate_frees_the_branch_so_the_fixed_rerun_lands(tmp_path):
    flag = tmp_path / "flag"
    repo = _repo_generating(tmp_path, f"cat {flag} > generated.txt")
    cli._land_execute(repo, [STEP, {"kind": "push", "branch": "pr/x"}], [], None, None, "auto", False, forge_github)
    wt = cli._land_worktree(repo, "pr/x")
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
    assert _git(repo, "branch", "--list", "pr/x") == ""
    flag.write_text("fixed")
    assert cli._land_resume(repo, STEP, NO_OPEN_PRS) == {"kind": "fresh"}
    ok, detail = cli._execute_land_step(repo, STEP)
    assert ok, detail
    assert cli._execute_land_step(repo, {"kind": "push", "branch": "pr/x"}) == (True, "pr/x")
    assert "generated.txt" in _git(repo, "show", "--name-only", "--format=", "origin/pr/x").split()


def test_a_rejected_amend_stops_the_land_like_a_failed_command(tmp_path, capsys):
    repo = _repo_generating(tmp_path, "echo generated > generated.txt")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho hook-said-no >&2\nexit 1\n")
    hook.chmod(0o755)
    rc, _, _ = cli._land_execute(repo, [STEP, {"kind": "push", "branch": "pr/x"}], [], None, None, "auto", False, forge_github)
    out = capsys.readouterr().out
    assert rc == 1
    assert "cherry_pick: generate failed: " in out and "hook-said-no" in out
    assert f"land: worktree left at {cli._land_worktree(repo, 'pr/x')} for inspection\n" in out
    assert _git(repo, "branch", "--list", "pr/x") == ""
    assert _git(repo, "ls-remote", "--heads", "origin", "pr/x") == ""


def test_a_generator_that_writes_its_worktree_path_still_resumes(tmp_path):
    repo = _repo_generating(tmp_path, "echo $COX_WORKTREE > where.txt")
    ok, detail = cli._execute_land_step(repo, STEP)
    assert ok, detail
    assert cli._execute_land_step(repo, {"kind": "push", "branch": "pr/x"}) == (True, "pr/x")
    assert cli._land_resume(repo, STEP, NO_OPEN_PRS) == {"kind": "resume", "local": True, "remote": True}


def test_a_rerun_after_the_push_is_recognised_as_already_pushed(tmp_path):
    repo = _repo_generating(tmp_path, "echo generated > generated.txt")
    ok, detail = cli._execute_land_step(repo, STEP)
    assert ok, detail
    assert cli._execute_land_step(repo, {"kind": "push", "branch": "pr/x"}) == (True, "pr/x")
    assert cli._land_resume(repo, STEP, NO_OPEN_PRS) == {"kind": "resume", "local": True, "remote": True}
    assert ".venv" not in _git(repo, "ls-tree", "-r", "--name-only", "origin/pr/x").split()


def test_a_generator_whose_output_varies_per_run_makes_the_rerun_refuse(tmp_path):
    repo = _repo_generating(tmp_path, "echo $$ > stamp.txt")
    ok, detail = cli._execute_land_step(repo, STEP)
    assert ok, detail
    assert cli._execute_land_step(repo, {"kind": "push", "branch": "pr/x"}) == (True, "pr/x")
    decision = cli._land_resume(repo, STEP, NO_OPEN_PRS)
    assert decision["kind"] == "refuse"
    assert "files differing: stamp.txt" in decision["reason"]


def test_the_umbrella_dir_reaches_only_the_cherry_pick_step():
    steps = [{"kind": "cherry_pick", "onto": "pr/x"}, {"kind": "push", "branch": "pr/x"}]
    enriched = cli._land_enrich(steps, path="p", worktree_root="w", umbrella="/u")
    assert enriched == [{"kind": "cherry_pick", "onto": "pr/x", "umbrella": "/u"}, {"kind": "push", "branch": "pr/x"}]
    assert route.parse_profile("umbrella_dir: /u\n") == {"umbrella_dir": "/u", "assume": "a"}
