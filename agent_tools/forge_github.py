"""The github forge: `gh` and `git push`. The protocol is in `agent_tools.forge`."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from agent_tools import land


def find_open_prs(repo: Path, branch: str) -> list[int] | str:
    """Numbers of the open PRs whose head is `branch`, or the reason they
    could not be listed."""
    try:
        r = subprocess.run(["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number"],
                           cwd=repo, capture_output=True, text=True)
    except OSError as exc:
        return f"could not list open pull requests for {branch}: {exc}"
    if r.returncode != 0:
        return f"could not list open pull requests for {branch}: {(r.stderr or r.stdout).strip()}"
    try:
        return [int(p["number"]) for p in json.loads(r.stdout or "[]")]
    except (ValueError, KeyError, TypeError):
        return f"could not read the open pull requests for {branch}: {r.stdout.strip()}"


def push(repo: Path, branch: str) -> tuple[bool, str]:
    r = subprocess.run(["git", "-C", str(repo), "push", "-u", "origin", branch], capture_output=True, text=True)
    return r.returncode == 0, (branch if r.returncode == 0 else r.stderr.strip() or r.stdout.strip())


def open_pr(repo: Path, title: str, body: str) -> tuple[bool, str]:
    r = subprocess.run(["gh", "pr", "create", "--title", title, "--body", body], cwd=repo, capture_output=True, text=True)
    return r.returncode == 0, (r.stdout.strip() or r.stderr.strip())


def merge(repo: Path, step: dict) -> tuple[bool, str]:
    r = subprocess.run(["gh", "pr", "merge", "--squash", "--delete-branch"], cwd=repo, capture_output=True, text=True)
    return r.returncode == 0, (r.stdout.strip() or r.stderr.strip())


def _read_checks(repo: Path):
    """`(True, (check_runs, status))` for HEAD's REST check bodies, or `(False, detail)` on a failed or unparseable call."""
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True)
    if head.returncode != 0:
        return False, head.stderr.strip() or "git rev-parse HEAD failed"
    argvs = land.rest_checks_argvs(head.stdout.strip())
    bodies = []
    for argv, key in zip(argvs, ("check_runs", "statuses")):
        r = subprocess.run(argv, cwd=repo, capture_output=True, text=True)
        body = land.merge_pages(r.stdout or "", key) if r.returncode == 0 else None
        if body is None:
            return False, (r.stderr or r.stdout or "").strip() or f"unreadable output from {argv[-1]}"
        bodies.append(body)
    return True, tuple(bodies)


def wait_checks(repo: Path, timeout_s: float, sleep=time.sleep, now=time.monotonic) -> tuple[bool, str]:
    # Edge bend (A2): a count of consecutive unreadable polls, reset by any readable one.
    errors = 0

    def poll() -> tuple[int, str]:
        nonlocal errors
        ok, value = _read_checks(repo)
        errors = 0 if ok else errors + 1
        if ok:
            return land.check_poll_result(*value)
        result = land.unreadable_poll(errors, value)
        if land.is_pending(result[0]):
            sleep(land.poll_backoff_s(errors))  # on top of the 15s between polls: a rate limit needs room
        return result
    return land.await_checks(poll, timeout_s, sleep, now)
