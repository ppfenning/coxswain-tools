"""`squash_phase`: the phase branch folded into its own pr branch as one commit, and the enrichment that
carries the umbrella and each task's own work item onto it, as `tests/test_land_generate.py` does for `cherry_pick`."""

import subprocess as sp

import pytest

from agent_tools import cli


def _git(repo, *args):
    return sp.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(autouse=True)
def _own_tempdir(tmp_path, monkeypatch):
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path / "tmp"))


def _repo_with_phase_branch(tmp_path, *, diverge=False):
    """`main` with one commit, an `epic/init/ph` branch one commit ahead of it. `diverge=True` also
    advances `main` past the branch point on the same line the branch touched, so a squash conflicts."""
    root = tmp_path / "repo"
    sp.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    for args in (["config", "user.email", "t@e"], ["config", "user.name", "t"]):
        _git(root, *args)
    (root / "f.txt").write_text("original\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    _git(root, "checkout", "-qb", "epic/init/ph")
    (root / "f.txt").write_text("epic change\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "phase work")
    _git(root, "checkout", "-q", "main")
    if diverge:
        (root / "f.txt").write_text("main change\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "main moves")
    return root


_STEP = {"kind": "squash_phase", "branch": "epic/init/ph", "onto": "pr/init--ph", "from": "main",
         "subject": "epic init: ph"}


def test_a_clean_squash_leaves_one_commit_on_the_pr_branch_with_the_subject(tmp_path):
    repo = _repo_with_phase_branch(tmp_path)
    ok, detail = cli._execute_land_step(repo, _STEP)
    assert ok, detail
    assert _git(repo, "rev-list", "--count", "main..pr/init--ph") == "1"
    assert _git(repo, "log", "-1", "--format=%s", "pr/init--ph") == "epic init: ph"


def test_a_conflicting_squash_returns_the_file_names_and_leaves_no_pr_branch(tmp_path):
    repo = _repo_with_phase_branch(tmp_path, diverge=True)
    ok, detail = cli._execute_land_step(repo, _STEP)
    assert not ok
    assert detail == "squash of epic/init/ph conflicts in: f.txt"
    assert _git(repo, "branch", "--list", "pr/init--ph") == ""


def test_a_squash_that_adds_nothing_is_refused_and_leaves_no_pr_branch_or_worktree(tmp_path):
    repo = _repo_with_phase_branch(tmp_path)
    _git(repo, "merge", "-q", "--ff-only", "epic/init/ph")
    ok, detail = cli._execute_land_step(repo, _STEP)
    assert (ok, detail) == (False, "epic/init/ph adds nothing over main")
    assert _git(repo, "branch", "--list", "pr/init--ph") == ""
    assert not cli._land_worktree(repo, "pr/init--ph").exists()


def _scratch_branch_gone(repo):
    return _git(repo, "branch", "--list", cli._scratch_merge_branch("epic/init/ph")) == "" \
        and not cli._land_worktree(repo, cli._scratch_merge_branch("epic/init/ph")).exists()


def test_a_branch_reintroducing_an_already_landed_parent_squashes_cleanly(tmp_path):
    """`epic/init/parent` squash-lands on `main` as one commit. `epic/init/ph` was built on the parent's
    own (unsquashed) branch and goes on to change the same file again, so `main` is not an ancestor of it
    and the direct content it shares with `main` lives only at an earlier commit on its own history."""
    root = tmp_path / "repo"
    sp.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    for args in (["config", "user.email", "t@e"], ["config", "user.name", "t"]):
        _git(root, *args)
    (root / "f.txt").write_text("original\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    _git(root, "checkout", "-qb", "epic/init/parent")
    (root / "g.txt").write_text("parent v1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "parent work")
    _git(root, "checkout", "-qb", "epic/init/ph")
    (root / "g.txt").write_text("parent v1\nchild addition\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "child work")
    _git(root, "checkout", "-q", "main")
    _git(root, "merge", "-q", "--squash", "epic/init/parent")
    _git(root, "commit", "-qm", "epic init: parent")

    ok, detail = cli._execute_land_step(root, _STEP)
    assert ok, detail
    assert "conflicts in" not in detail
    assert "child addition" in _git(root, "show", "pr/init--ph:g.txt")
    assert _scratch_branch_gone(root)


def test_a_real_conflict_not_a_landed_duplicate_still_refuses_and_names_the_file(tmp_path):
    repo = _repo_with_phase_branch(tmp_path, diverge=True)
    ok, detail = cli._execute_land_step(repo, _STEP)
    assert not ok
    assert detail == "squash of epic/init/ph conflicts in: f.txt"
    assert _git(repo, "branch", "--list", "pr/init--ph") == ""
    assert _scratch_branch_gone(repo)


def test_two_functions_appended_to_the_same_test_file_are_both_kept(tmp_path):
    root = tmp_path / "repo"
    sp.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    for args in (["config", "user.email", "t@e"], ["config", "user.name", "t"]):
        _git(root, *args)
    (root / "tests").mkdir()
    base = "def test_base():\n    assert True\n"

    def commit_file(text, msg):
        (root / "tests" / "test_x.py").write_text(text)
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", msg)

    commit_file(base, "init")
    _git(root, "checkout", "-qb", "epic/init/ph")
    commit_file(base + "\n\ndef test_phase():\n    assert True\n", "phase work")
    _git(root, "checkout", "-q", "main")
    commit_file(base + "\n\ndef test_main():\n    assert True\n", "main moves")

    ok, detail = cli._execute_land_step(root, _STEP)
    assert ok, detail
    assert detail == "squashed epic/init/ph onto pr/init--ph; resolved: append-only in tests/test_x.py"
    text = _git(root, "show", "pr/init--ph:tests/test_x.py")
    assert "<<<<<<<" not in text and "=======" not in text
    assert text.index("test_phase") < text.index("test_main")
    assert _scratch_branch_gone(root)


def _repo_with_test_file(tmp_path, name="test_x.py", body="def test_base():\n    assert True\n"):
    root = tmp_path / "repo"
    sp.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    for args in (["config", "user.email", "t@e"], ["config", "user.name", "t"]):
        _git(root, *args)
    (root / "tests").mkdir()
    (root / "tests" / name).write_text(body)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    return root


def _commit_on(root, branch, name, text, msg, create=False):
    _git(root, "checkout", *(["-qb"] if create else ["-q"]), branch)
    (root / "tests" / name).write_text(text)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", msg)
    _git(root, "checkout", "-q", "main")


def _refused(root, expect):
    ok, detail = cli._execute_land_step(root, _STEP)
    assert (ok, detail) == (False, f"squash of epic/init/ph conflicts in: {expect}")
    assert _git(root, "branch", "--list", "pr/init--ph") == ""
    assert _scratch_branch_gone(root)


def test_main_deleting_a_test_file_the_branch_modified_is_not_an_append(tmp_path):
    root = _repo_with_test_file(tmp_path)
    _commit_on(root, "epic/init/ph", "test_x.py", "def test_base():\n    assert False\n", "phase edits", create=True)
    _git(root, "rm", "-q", "tests/test_x.py")
    _git(root, "commit", "-qm", "main deletes")
    _refused(root, "tests/test_x.py")


def test_a_json_file_under_tests_added_on_both_sides_is_refused(tmp_path):
    root = _repo_with_test_file(tmp_path)
    _commit_on(root, "epic/init/ph", "data.json", '{"a": 1}\n', "phase data", create=True)
    _commit_on(root, "main", "data.json", '{"b": 2}\n', "main data")
    _refused(root, "tests/data.json")


def test_the_same_test_name_appended_on_both_sides_is_refused(tmp_path):
    root = _repo_with_test_file(tmp_path)
    base = "def test_base():\n    assert True\n"
    _commit_on(root, "epic/init/ph", "test_x.py", base + "\n\ndef test_same():\n    assert 1\n", "phase", create=True)
    _commit_on(root, "main", "test_x.py", base + "\n\ndef test_same():\n    assert 2\n", "main")
    _refused(root, "tests/test_x.py")


def test_a_squash_merge_conflict_in_appended_tests_is_resolved_with_the_suffix(tmp_path, monkeypatch):
    """`main` is an ancestor of the branch, so no scratch merge runs; the pr worktree gets its own commit
    appending to the same file just before the squash, which is what makes the squash merge itself conflict."""
    root = _repo_with_test_file(tmp_path)
    base = "def test_base():\n    assert True\n"
    _commit_on(root, "epic/init/ph", "test_x.py", base + "\n\ndef test_phase():\n    assert True\n", "phase", create=True)
    link_venv = cli._link_venv

    def link_then_diverge(repo, wt):
        link_venv(repo, wt)
        (wt / "tests" / "test_x.py").write_text(base + "\n\ndef test_pr():\n    assert True\n")
        _git(wt, "commit", "-qam", "pr worktree appends")

    monkeypatch.setattr(cli, "_link_venv", link_then_diverge)
    ok, detail = cli._execute_land_step(root, _STEP)
    assert ok, detail
    assert detail == "squashed epic/init/ph onto pr/init--ph; resolved: append-only in tests/test_x.py"
    text = _git(root, "show", "pr/init--ph:tests/test_x.py")
    assert "<<<<<<<" not in text
    assert text.index("test_pr") < text.index("test_phase")


CONFLICTED = "def test_a():\n    pass\n<<<<<<< HEAD\ndef test_b():\n    pass\n||||||| base\n=======\ndef test_c():\n    pass\n>>>>>>> theirs\n"


def test_append_only_resolution_writes_nothing_unless_every_file_resolves(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "src").mkdir()
    for name in ("tests/a.py", "src/b.py"):
        (tmp_path / name).write_text(CONFLICTED)
    (tmp_path / "tests" / "plain.py").write_text("def test_p():\n    pass\n")
    assert not cli._resolve_append_only(tmp_path, ["tests/a.py", "src/b.py"])
    assert not cli._resolve_append_only(tmp_path, ["tests/a.py", "tests/plain.py"])
    assert not cli._resolve_append_only(tmp_path, ["tests/a.py", "tests/missing.py"])
    assert not cli._resolve_append_only(tmp_path, [])
    assert (tmp_path / "tests" / "a.py").read_text() == CONFLICTED
    assert (tmp_path / "src" / "b.py").read_text() == CONFLICTED


def test_a_failed_stage_is_not_reported_as_resolved(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "a.py").write_text(CONFLICTED)
    assert not cli._resolve_append_only(tmp_path, ["tests/a.py"])
    assert "resolved" not in capsys.readouterr().out


def test_a_branch_already_containing_main_squashes_without_a_scratch_worktree(tmp_path):
    root = tmp_path / "repo"
    sp.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    for args in (["config", "user.email", "t@e"], ["config", "user.name", "t"]):
        _git(root, *args)
    (root / "f.txt").write_text("original\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    (root / "f.txt").write_text("main v2\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "main moves")
    _git(root, "checkout", "-qb", "epic/init/ph")
    (root / "g.txt").write_text("branch content\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "phase work")

    ok, detail = cli._execute_land_step(root, _STEP)
    assert ok, detail
    assert _git(root, "rev-list", "--count", "main..pr/init--ph") == "1"
    assert _git(root, "log", "-1", "--format=%s", "pr/init--ph") == "epic init: ph"
    assert _scratch_branch_gone(root)


def test_the_land_walk_drops_the_squash_worktree_and_keeps_the_pr_branch(tmp_path):
    repo = _repo_with_phase_branch(tmp_path)
    rc, reached, _ = cli._land_execute(repo, [_STEP], [_STEP], None, None, "full", False)
    assert (rc, reached) == (0, ["squash_phase"])
    assert not cli._land_worktree(repo, "pr/init--ph").exists()
    assert _git(repo, "branch", "--list", "pr/init--ph") == "pr/init--ph"


def test_clean_phase_after_squash_phase_leaves_no_pr_branch(tmp_path):
    repo = _repo_with_phase_branch(tmp_path)
    clean = {"kind": "clean_phase", "run": "r", "phase_branch": "epic/init/ph", "tasks": [],
             "worktree_root": str(tmp_path / "wts"), "pr_branch": "pr/init--ph"}
    assert cli._execute_land_step(repo, _STEP)[0]
    assert cli._execute_land_step(repo, clean)[0]
    assert _git(repo, "branch", "--list", "pr/init--ph", "epic/init/ph") == ""


def test_phase_task_items_names_only_approved_items():
    items = [{"id": "a", "status": "approved", "file": "/w/a.md"}, {"id": "d", "status": "done", "file": "/w/d.md"}]
    assert cli._phase_task_items(items) == {"a": "/w/a.md"}


def test_land_enrich_gives_squash_phase_the_umbrella_and_phase_mark_done_its_item():
    steps = [{"kind": "squash_phase", "onto": "pr/init--ph"}, {"kind": "mark_done", "task": "t1"},
             {"kind": "mark_done", "task": "t2"}]
    enriched = cli._land_enrich(steps, path="p", worktree_root="w", umbrella="/u",
                                 task_items={"t1": "work/init/ph/t1.md"})
    assert enriched[0] == {"kind": "squash_phase", "onto": "pr/init--ph", "umbrella": "/u"}
    assert enriched[1] == {"kind": "mark_done", "task": "t1", "path": "p",
                           "item": "work/init/ph/t1.md", "from": "approved", "to": "done"}
    assert enriched[2] == {"kind": "mark_done", "task": "t2", "path": "p"}
