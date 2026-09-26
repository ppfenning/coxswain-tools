"""Pure rules for a lane that runs on another machine. No file, clock or
network access: the edge passes host, launched_at and the taken ids in."""

from __future__ import annotations

import json
from pathlib import Path


def remote_record(host: str, launched_at: str, repo: str | None = None) -> dict:
    """`repo` is the chair repo the run was launched against; a record from before it was kept has none."""
    base = {"host": host, "launched_at": launched_at}
    return base if repo is None else {**base, "repo": repo}


def remote_record_path(runs_dir: Path, run: str) -> Path:
    return runs_dir / f"{run}.remote.json"


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


def land_needs_fetch(has_remote_record: bool, has_task_records: bool) -> bool:
    return has_remote_record and not has_task_records


def unfetched(remote_runs: list[str], fetched: set[str] | frozenset[str]) -> list[str]:
    return [run for run in remote_runs if run not in fetched]
