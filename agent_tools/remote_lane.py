"""Pure rules for a lane that runs on another machine. No file, clock or
network access: the edge passes host, launched_at and the taken ids in."""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from pathlib import Path


def remote_record(host: str, launched_at: str, repo: str | None = None) -> dict:
    """`repo` is the chair repo the run was launched against; a record from before it was kept has none."""
    base = {"host": host, "launched_at": launched_at}
    return base if repo is None else {**base, "repo": repo}


def remote_record_path(runs_dir: Path, run: str) -> Path:
    return runs_dir / f"{run}.remote.json"


def fetched_record_path(runs_dir: Path, run: str) -> Path:
    return runs_dir / f"{run}.fetched.json"


def parse_remote_record(text: str) -> dict | None:
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    if isinstance(parsed, dict) and "host" in parsed and "launched_at" in parsed:
        repo = parsed.get("repo")
        return remote_record(parsed["host"], parsed["launched_at"], repo if isinstance(repo, str) else None)
    return None


def refuse_taken_run_id(run_id: str, taken: set[str] | frozenset[str]) -> str | None:
    if run_id in taken:
        return f"run id {run_id} is already taken"
    return None


def land_needs_fetch(has_remote_record: bool, fetched: bool) -> bool:
    return has_remote_record and not fetched


def unfetched(remote_runs: list[str], fetched: set[str] | frozenset[str]) -> list[str]:
    return [run for run in remote_runs if run not in fetched]


def records_already_in_store(expected: Collection[str], in_store: Collection[str]) -> bool:
    """An empty `expected` is False: an unknown listing never counts as complete."""
    return bool(expected) and all(task in in_store for task in expected)


def all_landed(states: Mapping[str, str | None]) -> bool:
    """An empty `states` is False: a run with no known tasks never counts as landed."""
    return bool(states) and all(state == "done" for state in states.values())


def landed_elsewhere(states: Mapping[str, str | None], listed: Collection[str] | None, ended: bool) -> bool:
    """`listed` is the remote's task ids, None when the listing failed. The store only names tasks that reported, so a
    live run, a remote task the store lacks, or a remote that cannot be listed vetoes the skip: absence of a listing is not
    an empty listing.

    A task is done per initiative, not per run, so a retry under another run id also counts; the ticket accepts that."""
    return ended and listed is not None and all_landed(states) and all(task in states for task in listed)


def landed_elsewhere_line(run: str) -> str:
    return f"{run}: every task is done in the store; landed on another machine, nothing to fetch"


def landed_elsewhere_marker(fetched_at: str) -> dict:
    return {"fetched_at": fetched_at, "repos": [], "landed_elsewhere": True}


def is_landed_elsewhere_marker(text: str | None) -> bool:
    """True when the `.fetched.json` text was written by the landed-elsewhere skip; None or unreadable text is False."""
    try:
        parsed = json.loads(text) if text is not None else None
    except ValueError:
        return False
    return isinstance(parsed, dict) and parsed.get("landed_elsewhere") is True


def fetch_scope(store_complete: bool) -> str:
    if store_complete:
        return "branches"
    return "records-and-branches"
