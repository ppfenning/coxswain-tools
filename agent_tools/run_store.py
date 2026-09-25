"""A run's usage: the `<run_id>.usage.json` file first, the read-only SQLite
store `cox.db` only once that file is gone, and only for a run whose `runs` row
has an `ended_at`. Calls alone do not mean the run is done: graphs writes each
call as it finishes. `usages` lists every run that way, and `run_started`
reads a run's `launched_at`. Reads only; never creates, migrates or writes the
store."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

__all__ = ["call_from_row", "connect_readonly", "run_started", "summarize", "usage", "usages"]

STORE_FILENAME = "cox.db"

# Columns that carry over unchanged from a node_calls row to a usage-file call.
_SAME = (
    "role", "task_id", "tier", "cost_usd", "ceiling_usd", "ceiling_source", "turns", "duration_ms",
    "input_tokens", "cache_read_tokens", "cache_creation_tokens", "input_total", "output_tokens", "ts",
)


def summarize(calls: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Pure: totals and a per-model breakdown over the recorded calls."""
    fields = ("input_tokens", "cache_read_tokens", "cache_creation_tokens", "output_tokens")

    def total(call: Mapping[str, Any]) -> int:
        # Older records carried only a summed `input_tokens`; newer ones split it.
        return int(call.get("input_total") if call.get("input_total") is not None else call.get("input_tokens") or 0)

    by_model: dict[str, dict[str, Any]] = {}
    for call in calls:
        row = by_model.setdefault(str(call.get("model")), {"calls": 0, "cost_usd": 0.0, "input_total": 0, **dict.fromkeys(fields, 0)})
        row["calls"] += 1
        row["cost_usd"] = round(row["cost_usd"] + float(call.get("cost_usd") or 0.0), 4)
        row["input_total"] += total(call)
        for f in fields:
            row[f] += int(call.get(f) or 0)
    return {
        "calls": len(calls),
        "cost_usd": round(sum(float(c.get("cost_usd") or 0.0) for c in calls), 4),
        "turns": sum(int(c.get("turns") or 0) for c in calls),
        "input_total": sum(total(c) for c in calls),
        **{f: sum(int(c.get(f) or 0) for c in calls) for f in fields},
        "by_model": by_model,
    }


def call_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """One `node_calls` row as a usage-file call."""
    decision = row["decision_json"]
    return {
        **{k: row[k] for k in _SAME},
        "id": row["call_id"],
        "model": row["model_alias"],
        "ok": bool(row["ok"]),
        "decision": None if decision is None else json.loads(decision),
    }


def connect_readonly(runs_dir: Path) -> sqlite3.Connection | None:
    """None when `cox.db` is absent. Opened `mode=ro`, so it can never create the file."""
    path = (Path(runs_dir) / STORE_FILENAME).resolve()
    return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) if path.exists() else None


def _read_file(path: Path) -> dict | None:
    try:
        loaded = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _read_calls(runs_dir: Path, run_id: str) -> list[dict]:
    conn = connect_readonly(runs_dir)
    if conn is None:
        return []
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT n.* FROM node_calls n JOIN runs r ON r.run_id = n.run_id "
            "WHERE n.run_id = ? AND r.ended_at IS NOT NULL ORDER BY n.ts, n.seq",
            (run_id,),
        ).fetchall()
    except sqlite3.DatabaseError:
        return []
    finally:
        conn.close()
    return [call_from_row(r) for r in rows]


def usage(runs_dir: Path, run_id: str) -> dict | None:
    """The usage file unchanged when it parses as an object, else the store's rows for an ended run, else None."""
    from_file = _read_file(Path(runs_dir) / f"{run_id}.usage.json")
    if from_file is not None:
        return from_file
    calls = _read_calls(Path(runs_dir), run_id)
    return _store_usage(run_id, calls) if calls else None


def _store_usage(run_id: str, calls: list[dict]) -> dict:
    return {"run_id": run_id, "calls": calls, "summary": summarize(calls)}


def _store_runs(runs_dir: Path) -> dict[str, list[dict]]:
    """Every run with `node_calls` rows and an ended `runs` row, its calls ordered by ts then seq."""
    conn = connect_readonly(runs_dir)
    if conn is None:
        return {}
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT n.* FROM node_calls n JOIN runs r ON r.run_id = n.run_id "
            "WHERE r.ended_at IS NOT NULL ORDER BY n.run_id, n.ts, n.seq"
        ).fetchall()
    except sqlite3.DatabaseError:
        return {}
    finally:
        conn.close()
    by_run: dict[str, list[dict]] = {}
    for row in rows:
        by_run.setdefault(row["run_id"], []).append(call_from_row(row))
    return by_run


def usages(runs_dir: Path) -> dict[str, dict]:
    """Run id to usage: every parsing usage file, then each ended store run that has no file."""
    files = {
        path.name.removesuffix(".usage.json"): body
        for path in sorted(Path(runs_dir).glob("*.usage.json"))
        if (body := _read_file(path)) is not None
    }
    stored = {rid: _store_usage(rid, calls) for rid, calls in _store_runs(Path(runs_dir)).items() if rid not in files}
    return {**files, **stored}


def run_started(runs_dir: Path, run_id: str) -> str | None:
    """The `launched_at` of the store's `runs` row, else None."""
    conn = connect_readonly(Path(runs_dir))
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT launched_at FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()
    return None if row is None else row[0]
