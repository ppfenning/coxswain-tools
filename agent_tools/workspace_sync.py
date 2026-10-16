"""Pull and push the chair's workspace checkout; each returns "" on success or a one-line failure reason, and never raises."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

GIT_TIMEOUT_SECONDS = 30
_TIMEOUT_CODE = 124
_UNRUNNABLE_CODE = 127
_NOT_A_REPO = "not a git repository"
_UPSTREAM_FORMAT = "--format=%(HEAD)%09%(upstream:remotename)%09%(upstream:remoteref)"


def failure_line(verb: str, returncode: int, stderr: str) -> str:
    """Return "" for exit code 0, else `workspace <verb> failed: <first non-blank stderr line>`."""
    if returncode == 0:
        return ""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return f"workspace {verb} failed: {(lines or [f'exit code {returncode}'])[0]}"


def probe_line(verb: str, returncode: int, toplevel: str, stderr: str, workspace: str) -> str | None:
    """None when `workspace` is the top of a checkout; "" when it is not a checkout; else a failure line."""
    if returncode == 0:
        return None if toplevel.strip() == workspace else ""
    if _NOT_A_REPO in stderr:
        return ""
    return failure_line(verb, returncode, stderr)


def parse_upstream(listing: str) -> tuple[str, str] | None:
    """Return (remote, remote ref) of the current branch from a `%(HEAD)<TAB>remote<TAB>ref` listing; None when it has no upstream."""
    for line in listing.splitlines():
        head, _, rest = line.partition("\t")
        remote, _, ref = rest.partition("\t")
        if head == "*" and remote and ref:
            return remote, ref
    return None


def _git(workspace: Path, *args: str) -> tuple[int, str, str]:
    """Run git in `workspace` and return (exit code, stdout, stderr); a timeout or unrunnable git becomes a nonzero code and a reason."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        done = subprocess.run(
            ["git", *args], cwd=workspace, env=env, capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        return _TIMEOUT_CODE, "", f"git timed out after {GIT_TIMEOUT_SECONDS}s"
    except OSError as exc:
        return _UNRUNNABLE_CODE, "", f"cannot run git: {exc}"
    return done.returncode, done.stdout, done.stderr


def _upstream(workspace: Path, verb: str) -> tuple[str, tuple[str, str] | None]:
    """("", target) to sync, ("", None) for a no-op, (line, None) when the probe itself failed."""
    if not workspace.is_dir():
        return "", None
    code, out, err = _git(workspace, "rev-parse", "--show-toplevel")
    line = probe_line(verb, code, out, err, str(workspace.resolve()))
    if line is not None:
        return line, None
    code, out, err = _git(workspace, "for-each-ref", _UPSTREAM_FORMAT, "refs/heads")
    return (failure_line(verb, code, err), None) if code != 0 else ("", parse_upstream(out))


def _unmerged(workspace: Path) -> bool:
    """True when the index holds unmerged paths; read from git's state, not its messages."""
    code, out, _ = _git(workspace, "diff", "--name-only", "--diff-filter=U")
    return code == 0 and bool(out.strip())


def _undo_conflict(workspace: Path, head: str) -> str:
    """Put the tree back at `head` and re-apply the stash the failed autostash left behind."""
    for args in (("reset", "--hard", "--quiet", head), ("stash", "pop", "--quiet")):
        code, _, err = _git(workspace, *args)
        if code != 0:
            return failure_line("pull", code, err)
    return "workspace pull failed: stashed changes conflicted with the upstream update; checkout restored"


def pull_workspace(workspace: Path) -> str:
    """Fast-forward the checkout from its upstream, carrying unstaged changes; a non-checkout or a branch with no upstream is a no-op."""
    line, target = _upstream(workspace, "pull")
    if target is None:
        return line
    code, head, err = _git(workspace, "rev-parse", "HEAD")
    if code != 0:
        return failure_line("pull", code, err)
    code, _, err = _git(workspace, "pull", "--ff-only", "--autostash", "--quiet")
    # git leaves conflict markers and a stash entry behind when re-applying the autostash conflicts.
    if _unmerged(workspace):
        return _undo_conflict(workspace, head.strip())
    return failure_line("pull", code, err)


def push_workspace(workspace: Path) -> str:
    """Push the current branch to its upstream; a non-checkout or a branch with no upstream is a no-op."""
    line, target = _upstream(workspace, "push")
    if target is None:
        return line
    remote, ref = target
    code, _, err = _git(workspace, "push", "--quiet", remote, f"HEAD:{ref}")
    return failure_line("push", code, err)
