"""Loads the run corpus into the stats store (workspace/stats/stats.db).

Spec: docs/design/run-stats-store.md §3-§4. Spike verdict:
agent_tools/stats-join-spike.md — the calls-to-tasks join is heuristic and
out of scope here; `calls.task_id`/`calls.join_confidence` are left unset by
this ingester rather than guessed. Row-shaping (`run_row`, `call_rows`,
`recovered_call_rows`, `task_row`) is pure over already-parsed JSON; `load_run`,
`discover_runs` and `ingest` are the edge that reads files and writes the database.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_tools.events import Event, from_log
from agent_tools.records import load_trace
from agent_tools.runs_detail import NODE_ORDER
from agent_tools.stats_derive import _final_result, attempt_numbers, extract_failure_class, resolve_outcome
from agent_tools.stats_schema import CALLS_COLUMNS, RUNS_COLUMNS, TASKS_COLUMNS, connect

__all__ = [
    "IngestReport",
    "call_rows",
    "discover_runs",
    "ingest",
    "load_run",
    "recovered_call_rows",
    "run_row",
    "task_row",
]

SCHEMA_VERSION = 1


def run_row(
    run_id: str,
    usage: Mapping[str, Any] | None,
    node_records: Sequence[Mapping[str, Any]],
    launched: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """One `runs` row from a run's usage summary, its node records and its launch marker."""
    summary = (usage or {}).get("summary") or {}
    first_node = node_records[0] if node_records else {}
    minutes = sum(float(n.get("human_minutes") or 0.0) for n in node_records)
    return {
        "run_id": run_id,
        "started_at": summary.get("started_at"),
        "ended_at": summary.get("ended_at"),
        "cartridge_sha": first_node.get("cartridge_sha"),
        "cartridge_team": first_node.get("cartridge_team"),
        "provider_profile": None,
        "vendor": "claude-code",
        "host": None,
        "launched_by": (launched or {}).get("launched_by"),
        "principal": first_node.get("principal"),
        "human_minutes": minutes or None,
        "schema_version": SCHEMA_VERSION,
    }


def call_rows(run_id: str, usage: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """One `calls` row per model invocation in a run's usage record, in call order.
    `task_id`/`join_confidence` are left unset: assigning them is the heuristic
    join of a later ticket, not this one."""
    calls = list((usage or {}).get("calls") or [])
    attempts = attempt_numbers(calls)
    return [
        {
            "run_id": run_id,
            "seq": seq,
            "role": call.get("role"),
            "attempt": attempt,
            "tier": call.get("tier"),
            "model": call.get("model"),
            "cost_usd": call.get("cost_usd"),
            "turns": call.get("turns"),
            "duration_ms": call.get("duration_ms"),
            "input_tokens": call.get("input_tokens"),
            "cache_read_tokens": call.get("cache_read_tokens"),
            "cache_creation_tokens": call.get("cache_creation_tokens"),
            "output_tokens": call.get("output_tokens"),
            "tools": json.dumps(call["tools"]) if call.get("tools") is not None else None,
            "trace_path": call.get("trace"),
            "failure_class": None,
            "challenger": int(bool(call.get("challenger"))),
            "task_id": None,
            "join_confidence": None,
            "recovered_from_trace": 0,
        }
        for seq, (call, attempt) in enumerate(zip(calls, attempts))
    ]


def recovered_call_rows(run_id: str, traces: Sequence[tuple[str, Sequence[Mapping[str, Any]]]]) -> list[dict[str, Any]]:
    """One `calls` row per trace file, for a run whose usage record is absent (spec §1
    fact 5: a budget-stopped run writes no `usage.json` at all, so its cost survives
    only in each trace's terminal `type: result` line). `traces` is `(trace_path,
    parsed_events)` pairs already in `_read_traces`' NODE_ORDER order (role, then
    attempt index) so `seq` reflects execution order across roles, the same guarantee
    `_node_sort_key` gives `<run>:<node>.json` records; `role` comes from the
    `<role>-<n>.jsonl` stem, and `_final_result` — the same parser `extract_failure_class`
    already uses — is the only place `total_cost_usd` and `num_turns` are read from.
    Every row here carries `recovered_from_trace=1`, so a query can exclude or flag it
    the way `join_confidence` already flags a heuristic task join. Token counts,
    `duration_ms`, `tier` and `model` are unrecoverable from the result line and stay
    unset."""
    roles = [Path(path).stem.rsplit("-", 1)[0] for path, _ in traces]
    attempts = attempt_numbers([{"role": role} for role in roles])
    return [
        {
            "run_id": run_id,
            "seq": seq,
            "role": role,
            "attempt": attempt,
            "tier": None,
            "model": None,
            "cost_usd": _final_result(events).get("total_cost_usd"),
            "turns": _final_result(events).get("num_turns"),
            "duration_ms": None,
            "input_tokens": None,
            "cache_read_tokens": None,
            "cache_creation_tokens": None,
            "output_tokens": None,
            "tools": None,
            "trace_path": path,
            "failure_class": extract_failure_class(events, ""),
            "challenger": 0,
            "task_id": None,
            "join_confidence": None,
            "recovered_from_trace": 1,
        }
        for seq, (role, attempt, (path, events)) in enumerate(zip(roles, attempts, traces))
    ]


def _rounds(value: Any) -> int | None:
    """A list's length, a dict's own 'rounds' key, or None when neither shape is present."""
    if isinstance(value, list):
        return len(value)
    if isinstance(value, Mapping):
        return value.get("rounds")
    return None


def task_row(
    run_id: str,
    phase: str,
    ticket: str,
    record: Mapping[str, Any],
    gate_diffs: Sequence[Mapping[str, Any]],
    log_events: Sequence[Event],
) -> dict[str, Any]:
    """One `tasks` row from one task record, with `gate_diffs` (gathered from the
    run's node records) and `log_events` (parsed from the run's log) folded in so
    `resolve_outcome` sees the full picture and never guesses from a run-level
    budget stop, which names no ticket."""
    scoped_ticket = record.get("ticket", ticket)
    scoped = {**record, "ticket": scoped_ticket, "gate_diffs": gate_diffs, "log_events": log_events}
    outcome, outcome_source = resolve_outcome(scoped)
    arbitration = record.get("arbitration")
    attempt = record.get("attempt")
    return {
        "run_id": run_id,
        "task_id": record.get("run_id") or f"{run_id}:{phase}:{ticket}",
        "ticket": scoped_ticket,
        "phase": phase,
        "initiative": record.get("initiative"),
        "attempt": attempt if isinstance(attempt, int) else None,
        "outcome": outcome,
        "outcome_source": outcome_source,
        "review_rounds": _rounds(record.get("review")),
        "arbitration_verdict": arbitration.get("verdict") if isinstance(arbitration, Mapping) else None,
        "fix_loop_rounds": _rounds(record.get("fix_loop")),
        "cost_usd": record.get("cost_usd"),
        "reason": record.get("reason"),
    }


@dataclass(frozen=True)
class IngestReport:
    runs_ingested: int
    unparsed_count: int
    unparsed_sample: tuple[str, ...]


def discover_runs(runs_dir: Path) -> list[str]:
    """Run ids present under `runs_dir`, from `.usage.json`, `.launched.json`,
    `tasks/` stems, `<run>:<node>.json` records and `<run>-trace/` directories — a run
    that only ever wrote node records (no usage.json, no launched.json, no tasks/) is
    still a run, and so is one whose only surviving artifact is its trace directory
    (spec §1 fact 5: a budget-stopped run can write no usage.json at all)."""
    usage_ids = {p.name[: -len(".usage.json")] for p in runs_dir.glob("*.usage.json")}
    launched_ids = {p.name[: -len(".launched.json")] for p in runs_dir.glob("*.launched.json")}
    task_ids = {p.name for p in runs_dir.glob("*") if p.is_dir() and (p / "tasks").is_dir()}
    node_ids = {p.name.split(":", 1)[0] for p in runs_dir.glob("*:*.json")}
    trace_ids = {p.name.removesuffix("-trace") for p in runs_dir.glob("*-trace") if p.is_dir()}
    return sorted(usage_ids | launched_ids | task_ids | node_ids | trace_ids)


_NODE_INDEX = {name: i for i, name in enumerate(NODE_ORDER)}


def _node_sort_key(path: Path) -> tuple[int, str]:
    """A `<run>:<node>.json` path's position in the graph's own NODE_ORDER, so
    gate_diffs concatenate in execution order rather than filename alphabetical
    order; a node NODE_ORDER doesn't name sorts after every named one."""
    node = path.name.split(":", 1)[-1].removesuffix(".json")
    return (_NODE_INDEX.get(node, len(NODE_ORDER)), node)


def _read_json(path: Path, unparsed: list[str]) -> dict[str, Any] | None:
    """The parsed JSON object at `path`, or None with `path` appended to `unparsed`
    when the file is absent, is not valid JSON, is not readable as UTF-8, or fails
    to read at all, or parses to something other than an object."""
    if not path.exists():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        unparsed.append(str(path))
        return None
    if not isinstance(parsed, dict):
        unparsed.append(str(path))
        return None
    return parsed


def _read_log_lines(path: Path, unparsed: list[str]) -> list[str]:
    """A run's log lines, or `[]` with `path` appended to `unparsed` when the file
    is absent, is not readable as UTF-8, or fails to read at all."""
    if not path.exists():
        return []
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError):
        unparsed.append(str(path))
        return []


def _read_traces(trace_dir: Path, unparsed: list[str]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Every `<role>-<n>.jsonl` trace file under `trace_dir`, ordered by the graph's own
    NODE_ORDER (then by `n` within a role) — the same rule `_node_sort_key` already
    applies to `<run>:<node>.json` records, so a recovered run's calls come out in
    execution order rather than filename alphabetical order (`build` sorting before
    `decompose` would otherwise misorder `calls.seq`). A role NODE_ORDER doesn't name
    sorts after every named one. A name whose stem does not split into a role and a
    trailing integer, or a file that fails to read, is appended to `unparsed` instead
    of raising."""
    if not trace_dir.exists():
        return []
    keyed: list[tuple[tuple[int, str, int], Path]] = []
    for path in trace_dir.glob("*.jsonl"):
        role, _, idx = path.stem.rpartition("-")
        if not role or not idx.isdigit():
            unparsed.append(str(path))
            continue
        keyed.append(((_NODE_INDEX.get(role, len(NODE_ORDER)), role, int(idx)), path))
    pairs: list[tuple[str, list[dict[str, Any]]]] = []
    for _, path in sorted(keyed, key=lambda kv: kv[0]):
        try:
            pairs.append((str(path), load_trace(path)))
        except (OSError, UnicodeDecodeError):
            unparsed.append(str(path))
    return pairs


def load_run(runs_dir: Path, run_id: str) -> dict[str, Any]:
    """Every file this ingester reads for one run — usage, launch marker, node
    records, task records and log lines — plus the paths that failed to parse.
    Trace files are read only when `usage` is absent: a usage record's `calls[]` is
    authoritative, and `.usage.json` is exactly the file fact 5 says a budget-stopped
    run never wrote. A run whose usage.json IS present but undercounts a call (a
    different, already-measured symptom) is out of scope here and reads unrecovered,
    exactly as before this ticket."""
    unparsed: list[str] = []
    usage = _read_json(runs_dir / f"{run_id}.usage.json", unparsed)
    launched = _read_json(runs_dir / f"{run_id}.launched.json", unparsed)
    traces = _read_traces(runs_dir / f"{run_id}-trace", unparsed) if usage is None else []
    node_records = [
        parsed
        for path in sorted(runs_dir.glob(f"{run_id}:*.json"), key=_node_sort_key)
        for parsed in [_read_json(path, unparsed)]
        if parsed is not None
    ]
    tasks_root = runs_dir / run_id / "tasks"
    task_files = [
        (path.parent.name, path.stem, parsed)
        for path in sorted(tasks_root.glob("*/*.json"))
        for parsed in [_read_json(path, unparsed)]
        if parsed is not None
    ]
    log_lines = _read_log_lines(runs_dir / f"{run_id}.log", unparsed)
    return {
        "usage": usage,
        "launched": launched,
        "node_records": node_records,
        "task_files": task_files,
        "log_lines": log_lines,
        "traces": traces,
        "unparsed": unparsed,
    }


def _upsert(
    conn: sqlite3.Connection,
    run_id: str,
    run: Mapping[str, Any],
    calls: Sequence[Mapping[str, Any]],
    tasks: Sequence[Mapping[str, Any]],
) -> None:
    """Replaces this run_id's rows in all three tables, so ingesting the same directory twice is a no-op."""
    run_cols = ", ".join(c.name for c in RUNS_COLUMNS)
    run_placeholders = ", ".join(f":{c.name}" for c in RUNS_COLUMNS)
    conn.execute(f"INSERT OR REPLACE INTO runs ({run_cols}) VALUES ({run_placeholders})", run)

    conn.execute("DELETE FROM calls WHERE run_id = ?", (run_id,))
    call_cols = ", ".join(c.name for c in CALLS_COLUMNS)
    call_placeholders = ", ".join(f":{c.name}" for c in CALLS_COLUMNS)
    if calls:
        conn.executemany(f"INSERT INTO calls ({call_cols}) VALUES ({call_placeholders})", calls)

    conn.execute("DELETE FROM tasks WHERE run_id = ?", (run_id,))
    task_cols = ", ".join(c.name for c in TASKS_COLUMNS)
    task_placeholders = ", ".join(f":{c.name}" for c in TASKS_COLUMNS)
    if tasks:
        conn.executemany(f"INSERT INTO tasks ({task_cols}) VALUES ({task_placeholders})", tasks)


def ingest(runs_dir: Path | str, db_path: Path | str) -> IngestReport:
    """Upserts every run under `runs_dir` into the stats store at `db_path`, keyed
    by run_id so re-running over the same directory changes nothing. Errors before
    any write when `runs_dir` does not exist, or exists but holds no run records —
    an empty ingest is not a successful one (charter B4)."""
    runs_dir = Path(runs_dir)
    if not runs_dir.is_dir():
        raise FileNotFoundError(f"no such runs directory: {runs_dir.resolve()}")
    run_ids = discover_runs(runs_dir)
    if not run_ids:
        raise FileNotFoundError(f"no run records found under: {runs_dir.resolve()}")

    conn = connect(db_path)
    unparsed: list[str] = []
    for run_id in run_ids:
        loaded = load_run(runs_dir, run_id)
        unparsed.extend(loaded["unparsed"])
        gate_diffs = [d for node in loaded["node_records"] for d in (node.get("gate_diffs") or [])]
        log_events = from_log(run_id, loaded["log_lines"])
        run = run_row(run_id, loaded["usage"], loaded["node_records"], loaded["launched"])
        calls = (
            recovered_call_rows(run_id, loaded["traces"])
            if loaded["usage"] is None and loaded["traces"]
            else call_rows(run_id, loaded["usage"])
        )
        tasks = [
            task_row(run_id, phase, ticket, record, gate_diffs, log_events)
            for phase, ticket, record in loaded["task_files"]
        ]
        _upsert(conn, run_id, run, calls, tasks)
    conn.commit()
    conn.close()
    return IngestReport(runs_ingested=len(run_ids), unparsed_count=len(unparsed), unparsed_sample=tuple(unparsed[:5]))
