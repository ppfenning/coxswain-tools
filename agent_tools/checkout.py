"""Read a repository's file listing. The only place this feature touches disk or a process."""

import os
import subprocess
from pathlib import Path


def _git_listing(repo_path: Path) -> str | None:
    """NUL-separated paths on origin/main, or None when the ref cannot be read.

    Discovery stops at `repo_path`, so a plain directory inside some other checkout is not read as that checkout.
    """
    env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(repo_path.resolve().parent)}
    try:
        done = subprocess.run(
            ["git", "-C", str(repo_path), "ls-tree", "-r", "-z", "--name-only", "origin/main"],
            capture_output=True,
            check=False,
            env=env,
        )
    except OSError, subprocess.SubprocessError:
        return None
    return done.stdout.decode("utf-8", "surrogateescape") if done.returncode == 0 else None


def _walk_listing(repo_path: Path) -> frozenset[str]:
    """Repo-relative forward-slash paths of the working tree, .git skipped."""
    found = []
    for root, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        found.extend(Path(root, name).relative_to(repo_path).as_posix() for name in filenames)
    return frozenset(found)


def _choose_listing(git_out: str | None, walked: frozenset[str]) -> frozenset[str]:
    """The origin/main listing when the ref was read, else the walked working tree."""
    if git_out is not None:
        return frozenset(p for p in git_out.split("\0") if p)
    return walked


def read_checkout(repo_path: str | Path) -> frozenset[str]:
    """Repo-relative paths in the checkout; empty means the directory is missing."""
    path = Path(repo_path)
    if not path.is_dir():
        return frozenset()
    git_out = _git_listing(path)
    walked = frozenset() if git_out is not None else _walk_listing(path)
    return _choose_listing(git_out, walked)
