"""Pure rules for a lane that runs on another machine. No file, clock or
network access: the edge passes host, launched_at and the taken ids in."""

from __future__ import annotations

import json
from pathlib import Path


def remote_record(host: str, launched_at: str) -> dict:
    return {"host": host, "launched_at": launched_at}


def remote_record_path(runs_dir: Path, run: str) -> Path:
    return runs_dir / f"{run}.remote.json"


def parse_remote_record(text: str) -> dict | None:
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    if isinstance(parsed, dict) and "host" in parsed and "launched_at" in parsed:
        return remote_record(parsed["host"], parsed["launched_at"])
    return None


def refuse_taken_run_id(run_id: str, taken: set[str] | frozenset[str]) -> str | None:
    if run_id in taken:
        return f"run id {run_id} is already taken"
    return None


def land_needs_fetch(has_remote_record: bool, has_task_records: bool) -> bool:
    return has_remote_record and not has_task_records
