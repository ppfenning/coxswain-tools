"""Real git and gh adapters for the revert executor, and the main CI reader.

Every adapter takes a runner, a callable from an argv list to (exit code, output).
Nothing here spawns a process; the caller injects the runner that does.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Callable, Mapping

from agent_tools import chair_ci_jobs

__all__ = [
    "CI_WATCH_TIMEOUT_SECONDS",
    "LOG_TAIL_LINES",
    "GhForgePort",
    "GitRevertPort",
    "Runner",
    "backup_ref",
    "bounded",
    "find_open_revert_pr",
    "main_ci",
    "main_ci_run",
    "parent_count",
    "rerun_failed",
]

Runner = Callable[[list[str]], tuple[int, str]]

LOG_TAIL_LINES = 40
# The longest a revert port waits on any one command, `gh pr checks --watch` being the slow one.
CI_WATCH_TIMEOUT_SECONDS = 30 * 60
_PENDING_STATUSES = frozenset({"queued", "in_progress", "waiting", "requested", "pending"})
# Names tried for one backup ref before giving up; each try finds the previous name taken.
_BACKUP_TRIES = 50


def parent_count(rev_list_line: str) -> int:
    """Parents named on a `git rev-list --parents -n 1` line, whose first hash is the commit itself."""
    return max(len(rev_list_line.split()) - 1, 0)


def backup_ref(branch: str, tip: str, attempt: int) -> str:
    """Where a stale branch's tip is kept; `attempt` makes the name distinct from earlier backups of the same tip."""
    return f"refs/backup/{branch}/{tip[:12]}-{attempt}"


def _remote_tip(ls_remote_output: str, branch: str) -> str:
    """Hash of `refs/heads/<branch>` on a `git ls-remote` listing, or "" when the listing does not carry it."""
    rows = [line.split() for line in ls_remote_output.splitlines()]
    return next((row[0] for row in rows if len(row) == 2 and row[1] == f"refs/heads/{branch}"), "")


def bounded(run: Callable[..., tuple[int, str]], timeout: int = CI_WATCH_TIMEOUT_SECONDS) -> Runner:
    """A Runner that gives every call `timeout`. `run` reports expiry as a non-zero exit whose output names the bound."""
    return lambda argv: run(argv, timeout=timeout)


def _lines(output: str) -> list[str]:
    return [line.strip() for line in output.splitlines() if line.strip()]


def _checked(runner: Runner, argv: list[str]) -> str:
    code, output = runner(argv)
    if code != 0:
        raise RuntimeError(f"{' '.join(argv)} exited {code}: {output.strip()}")
    return output


class GitRevertPort:
    """Reverts in a throwaway worktree, so the shared checkout is never switched, reset or left on a branch."""

    def __init__(self, runner: Runner, mkdtemp: Callable[[], str] = lambda: tempfile.mkdtemp(prefix="revert-")) -> None:
        self._run = runner
        self._mkdtemp = mkdtemp
        # Branch to (worktree, temp dir) for a clean revert awaiting its push, which removes both.
        self._trees: dict[str, tuple[str, str]] = {}
        # Branch to the remote tip it replaces, "" when the remote had none; the push leases on it.
        self._leases: dict[str, str] = {}

    def fetch_main(self, repo: str) -> None:
        _checked(self._run, ["git", "-C", repo, "fetch", "origin", "main"])

    def _release(self, repo: str, branch: str, tree: tuple[str, str], drop_branch: bool) -> None:
        worktree, tmp = tree
        self._run(["git", "-C", repo, "worktree", "remove", "--force", worktree])
        if drop_branch:
            # Only after the worktree is gone: git refuses to delete a branch a worktree has checked out.
            self._run(["git", "-C", repo, "branch", "-D", branch])
        shutil.rmtree(tmp, ignore_errors=True)

    def revert_onto_branch(self, repo: str, branch: str, commit: str) -> list[str]:
        """Branch from origin/main and revert `commit` in a throwaway worktree.

        A local or remote `branch` left by an earlier attempt is backed up first and then replaced:
        the local one by `worktree add -B`, the remote one by the lease-guarded `push`. Callers that
        must keep a branch an open PR uses skip this call (see `perform_revert`'s `find_open_pr`).
        """
        parents = parent_count(_checked(self._run, ["git", "-C", repo, "rev-list", "--parents", "-n", "1", commit]))
        # A crashed attempt leaves its worktree registered though its directory is gone, and git refuses the branch.
        _checked(self._run, ["git", "-C", repo, "worktree", "prune"])
        local, remote = self._stale_tips(repo, branch)
        for tip in (local, remote):
            if tip:
                self._backup(repo, branch, tip)
        tmp = self._mkdtemp()
        tree = (f"{tmp}/wt", tmp)
        try:
            # -B, not -b: it resets a leftover local branch to origin/main instead of refusing it.
            _checked(self._run, ["git", "-C", repo, "worktree", "add", "-B", branch, tree[0], "origin/main"])
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        git = ["git", "-C", tree[0]]
        try:
            merge_flags = ["-m", "1"] if parents == 2 else []
            code, output = self._run([*git, "revert", "--no-edit", *merge_flags, commit])
            if code == 0:
                self._trees[branch] = tree
                self._leases[branch] = remote
                return []
            _, unmerged = self._run([*git, "diff", "--name-only", "--diff-filter=U"])
            self._run([*git, "revert", "--abort"])
        except BaseException:
            self._release(repo, branch, tree, drop_branch=True)
            raise
        self._release(repo, branch, tree, drop_branch=True)
        files = _lines(unmerged)
        if not files:
            # A failed revert with no unmerged files is not a conflict; [] would read as a clean revert.
            raise RuntimeError(f"git revert {commit} exited {code} with no conflicts: {output.strip()}")
        return files

    def _tip(self, argv: list[str]) -> str:
        """Hash a read-only rev-parse prints, or "" when the ref is not there."""
        code, output = self._run(argv)
        return output.strip() if code == 0 else ""

    def _backup(self, repo: str, branch: str, tip: str) -> str:
        for attempt in range(1, _BACKUP_TRIES + 1):
            ref = backup_ref(branch, tip, attempt)
            if not self._tip(["git", "-C", repo, "rev-parse", "--verify", "--quiet", ref]):
                _checked(self._run, ["git", "-C", repo, "update-ref", ref, tip])
                return ref
        raise RuntimeError(f"no free backup ref for {branch} at {tip[:12]} after {_BACKUP_TRIES} tries")

    def _stale_tips(self, repo: str, branch: str) -> tuple[str, str]:
        """Tips of the local and remote `branch`, "" for one that is absent; the remote tip is fetched so it is a local object."""
        git = ["git", "-C", repo]
        local = self._tip([*git, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"])
        # ls-remote, not the tracking ref: `fetch_main` fetches main only, so origin/<branch> may lag.
        listing = _checked(self._run, [*git, "ls-remote", "--heads", "origin", f"refs/heads/{branch}"])
        remote = _remote_tip(listing, branch)
        if remote:
            # The remote tip must be a local object before update-ref can point at it.
            _checked(self._run, [*git, "fetch", "origin", branch])
        return local, remote

    def clear_stale_branch(self, repo: str, branch: str, pr_open: bool) -> list[str]:
        """Back up and delete a local or remote `branch` no open PR uses; returns the backup refs written.

        Every backup is written before either delete. Only ref commands run: the checkout is never switched.
        Run it before `revert_onto_branch`, whose `worktree add -b` recreates the branch from origin/main.
        """
        if pr_open:
            return []
        git = ["git", "-C", repo]
        local, remote = self._stale_tips(repo, branch)
        backups = [self._backup(repo, branch, tip) for tip in (local, remote) if tip]
        if local:
            _checked(self._run, [*git, "branch", "-D", branch])
        if remote:
            _checked(self._run, [*git, "push", "origin", "--delete", branch])
        return backups

    def push(self, repo: str, branch: str) -> None:
        tree = self._trees.pop(branch, None)
        replaced = self._leases.pop(branch, "")
        if tree is None:
            _checked(self._run, ["git", "-C", repo, "push", "origin", branch])
            return
        # Replacing a leftover remote branch is a force push; the lease refuses it if the branch moved since it was read.
        lease = [f"--force-with-lease=refs/heads/{branch}:{replaced}"] if replaced else []
        try:
            _checked(self._run, ["git", "-C", tree[0], "push", *lease, "origin", branch])
        finally:
            # The remote branch is what the PR needs; the worktree has done its job whether or not the push landed.
            self._release(repo, branch, tree, drop_branch=False)


class GhForgePort:
    def __init__(self, runner: Runner) -> None:
        self._run = runner

    def open_pr(self, repo: str, branch: str, title: str, body: str) -> str:
        output = _checked(
            self._run,
            ["gh", "pr", "create", "--repo", repo, "--head", branch, "--title", title, "--body", body],
        )
        urls = [line for line in _lines(output) if line.startswith("http")]
        if not urls:
            raise RuntimeError(f"gh pr create printed no url: {output.strip()}")
        return urls[-1]

    def wait_checks(self, pr: str) -> list[str]:
        # Non-zero exit is how gh reports failing or pending checks, so the output is read regardless.
        # Piped, gh prints one tab-separated row per check: name, state, elapsed, url.
        # An empty list means green and the executor merges on it, so no rows or an unexplained exit raises.
        code, output = self._run(["gh", "pr", "checks", pr, "--watch"])
        rows = [line.split("\t") for line in output.splitlines() if "\t" in line]
        failing = [row[0].strip() for row in rows if len(row) > 1 and row[1].strip() == "fail"]
        if not rows or (code != 0 and not failing):
            raise RuntimeError(f"gh pr checks {pr} exited {code} with no failing check: {output.strip()}")
        return failing

    def merge(self, pr: str) -> None:
        _checked(self._run, ["gh", "pr", "merge", pr, "--squash"])


def find_open_revert_pr(runner: Runner, repo: str, pr: int | str) -> str | None:
    """URL of the open PR whose head branch is `revert/<pr>`, else None. A failed lookup raises, never reads as None."""
    head = f"revert/{pr}"
    listed = _checked(
        runner,
        ["gh", "pr", "list", "--repo", repo, "--state", "open", "--head", head, "--json", "number,url,headRefName"],
    )
    return next((p["url"] for p in json.loads(listed or "[]") if p.get("headRefName") == head), None)


def _judged_run(rows: list[dict], commit: str) -> dict | None:
    """The newest run (highest databaseId) whose headSha is `commit`; a row for any other commit is never judged."""
    matching = [row for row in rows if row.get("headSha") == commit]
    return max(matching, key=lambda row: row["databaseId"]) if matching else None


_PASSING_JOBS = ("success", "skipped", "neutral")


def _failed_jobs_line(payload: Mapping) -> str:
    jobs = payload.get("jobs") or []
    names = [job.get("name") or "unnamed" for job in jobs if job.get("conclusion") not in _PASSING_JOBS]
    return f"jobs never started: {', '.join(names)}"


def _jobs_payload(runner: Runner, repo: str, run_id: int) -> Mapping:
    """Parsed `gh run view --json jobs`; an unreadable or malformed answer is empty, which never reads as infra."""
    code, out = runner(["gh", "run", "view", str(run_id), "--repo", repo, "--json", "jobs"])
    try:
        payload = json.loads(out) if code == 0 else {}
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def main_ci_run(runner: Runner, repo: str, commit: str) -> tuple[str, str, int | None]:
    """`main_ci` plus the id of the run it judged, which `rerun_failed` takes; None when no run exists yet."""
    listed = _checked(
        runner,
        [
            "gh", "run", "list", "--repo", repo, "--branch", "main", "--commit", commit,
            "--json", "databaseId,status,conclusion,headSha",
        ],
    )  # fmt: skip
    run = _judged_run(json.loads(listed or "[]"), commit)
    if run is None:
        return "pending", "", None
    run_id = run["databaseId"]
    if run.get("status") in _PENDING_STATUSES:
        return "pending", "", run_id
    conclusion = run.get("conclusion")
    if conclusion == "success":
        return "green", "", run_id
    if conclusion != "failure":
        # Cancelled, skipped, neutral and the rest are not evidence that main is red.
        return "pending", f"run concluded {conclusion}", run_id
    jobs = _jobs_payload(runner, repo, run_id)
    if chair_ci_jobs.never_started(jobs):
        return "infra", _failed_jobs_line(jobs), run_id
    code, log = runner(["gh", "run", "view", str(run_id), "--repo", repo, "--log-failed"])
    tail = "\n".join(log.splitlines()[-LOG_TAIL_LINES:])
    return "red", tail if code == 0 else f"failing log unavailable, gh run view exited {code}: {tail}", run_id


def main_ci(runner: Runner, repo: str, commit: str) -> tuple[str, str]:
    """State of main's newest run whose headSha is `commit`: pending, green, red, or infra when no job got past setup."""
    state, output, _ = main_ci_run(runner, repo, commit)
    return state, output


def rerun_failed(runner: Runner, repo: str, run_id: int, commit: str) -> tuple[str, str, int | None]:
    """Rerun the failed jobs of `run_id`, wait for it to settle, and read main CI again for `commit`; the state may be infra.

    The wait is as bounded as `runner` is (see `bounded`): an expired wait is a non-zero exit and raises.
    """
    _checked(runner, ["gh", "run", "rerun", str(run_id), "--repo", repo, "--failed"])
    _checked(runner, ["gh", "run", "watch", str(run_id), "--repo", repo])
    return main_ci_run(runner, repo, commit)
