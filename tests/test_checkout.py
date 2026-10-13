import subprocess
from pathlib import Path

from agent_tools.checkout import _choose_listing, read_checkout


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _write(root: Path, rel: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x")


def test_origin_main_listing_ignores_untracked_file(tmp_path):
    _git(tmp_path, "init", "-q")
    _write(tmp_path, "src/a.py")
    _write(tmp_path, "README.md")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "init")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    _write(tmp_path, "untracked.txt")
    assert read_checkout(tmp_path) == frozenset({"src/a.py", "README.md"})


def test_plain_directory_falls_back_to_working_tree_without_git_dir(tmp_path):
    _write(tmp_path, "a/b.txt")
    _write(tmp_path, "top.txt")
    _write(tmp_path, ".git/config")
    assert read_checkout(tmp_path) == frozenset({"a/b.txt", "top.txt"})


def test_plain_directory_inside_another_repository_is_walked_not_read_as_that_repository(tmp_path):
    outer = tmp_path / "outer"
    _write(outer, "tracked.py")
    _git(outer, "init", "-q")
    _git(outer, "add", ".")
    _git(outer, "commit", "-q", "-m", "init")
    _git(outer, "update-ref", "refs/remotes/origin/main", "HEAD")
    _write(outer, "inner/only.txt")
    assert read_checkout(outer / "inner") == frozenset({"only.txt"})


def test_missing_directory_is_empty(tmp_path):
    assert read_checkout(tmp_path / "missing") == frozenset()


def test_choose_listing_by_literals():
    assert _choose_listing("a\0b/c\0", frozenset({"x"})) == frozenset({"a", "b/c"})
    assert _choose_listing(None, frozenset({"x"})) == frozenset({"x"})
    assert _choose_listing("", frozenset({"x"})) == frozenset(), "a readable ref with an empty tree is still the ref"
