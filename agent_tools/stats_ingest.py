"""Loads the run corpus into the stats store (workspace/stats/stats.db).

Spec: docs/design/run-stats-store.md §3-§4. Spike verdict:
agent_tools/stats-join-spike.md — the calls-to-tasks join is heuristic: a
run's build calls get `task_id`/`join_confidence='heuristic'` in ticket order
only where `sum(fix_loop.attempts)` over its task records equals its build
call count (171/184 runs in the spike); every other call, in every other run,
gets `join_confidence='none'` and no `task_id` rather than a guess. Row-shaping
(`run_row`, `call_rows`, `recovered_call_rows`, `task_row`, `run_join_holds`,
`assign_task_ids`, `fill_failure_classes`) is pure over already-parsed JSON;
`load_run`, `discover_runs` and `ingest` are the edge that reads files
(including each call's own trace and the autonomy ledger) and writes the database.
"""

from __future__ import annotations

import json
import socket
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_tools.events import Event, from_log
from agent_tools.records import load_trace
from agent_tools.route import parse_frontmatter
from agent_tools.runs_detail import NODE_ORDER
from agent_tools.stats_derive import _final_result, attempt_numbers, extract_failure_class, resolve_outcome
from agent_tools.stats_schema import CALLS_COLUMNS, RUNS_COLUMNS, TASKS_COLUMNS, connect

__all__ = [
    "IngestReport",
    "assign_task_ids",
    "call_rows",
    "discover_runs",
    "fill_failure_classes",
    "ingest",
    "load_run",
    "provider_profile_for",
    "provider_profile_from_nodes",
    "provider_profile_source",
    "recovered_call_rows",
    "run_join_holds",
    "run_row",
    "task_row",
]

LEDGER_PATH = Path.home() / ".local" / "state" / "agent-graphs" / "ledger.jsonl"

SCHEMA_VERSION = 1


def run_row(
    run_id: str,
    usage: Mapping[str, Any] | None,
    node_records: Sequence[Mapping[str, Any]],
    launched: Mapping[str, Any] | None,
    host: str | None = None,
    provider_profile: str | None = None,
) -> dict[str, Any]:
    """One `runs` row from a run's usage summary, its node records and its launch
    marker. `host` names the ingesting machine, not necessarily the one the run
    executed on — a backfilled, inferred value, not an observed one — and
    `provider_profile` comes from the autonomy ledger or, failing that, the run's own
    node records; both are the edge's job to resolve and pass in, never read here."""
    summary = (usage or {}).get("summary") or {}
    first_node = node_records[0] if node_records else {}
    minutes = sum(float(n.get("human_minutes") or 0.0) for n in node_records)
    return {
        "run_id": run_id,
        "started_at": summary.get("started_at"),
        "ended_at": summary.get("ended_at"),
        "cartridge_sha": first_node.get("cartridge_sha"),
        "cartridge_team": first_node.get("cartridge_team"),
        "provider_profile": provider_profile,
        "vendor": "claude-code",
        "host": host,
        "launched_by": (launched or {}).get("launched_by"),
        "principal": first_node.get("principal"),
        "human_minutes": minutes or None,
        "schema_version": SCHEMA_VERSION,
    }


def call_rows(run_id: str, usage: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """One `calls` row per model invocation in a run's usage record, in call order.
    `task_id`/`join_confidence`/`failure_class` are left unset here: assigning them
    needs this run's task records and its calls' own trace files, which only the
    edge (`ingest`, via `assign_task_ids`/`fill_failure_classes`) has."""
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
            "failure_class": extract_failure_class(events, "", role),
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


def _fix_loop_attempts(record: Mapping[str, Any]) -> int:
    """A task record's build-attempt count for the join identity (stats-join-spike.md):
    `fix_loop`'s own 'attempts' field when `fix_loop` is a dict, its length when
    `fix_loop` is a list of rounds, or 1 when `fix_loop` is absent — a task that never
    entered the fix loop still made its one build call."""
    fix_loop = record.get("fix_loop")
    if isinstance(fix_loop, Mapping):
        attempts = fix_loop.get("attempts")
        return attempts if isinstance(attempts, int) else 1
    if isinstance(fix_loop, list):
        return len(fix_loop) or 1
    return 1


def run_join_holds(attempts: Sequence[int], build_call_count: int) -> bool:
    """True when this run's counting identity holds: `sum(attempts)` — one entry
    per task record, each from `_fix_loop_attempts` — equals its build call count.
    The spike verifies this in 171/184 runs; a run where it fails gets no task_id."""
    return sum(attempts) == build_call_count


def assign_task_ids(
    calls: Sequence[Mapping[str, Any]], task_ids: Sequence[str], attempts: Sequence[int]
) -> list[dict[str, Any]]:
    """Sets `task_id`/`join_confidence` on every one of this run's calls. When
    `run_join_holds`, each build call gets the ticket at its position in
    `task_ids` repeated `attempts` times (ticket order x attempts) and
    `join_confidence='heuristic'`; every call otherwise — every call in this run
    when the identity fails, and every non-build call when it holds — gets no
    `task_id` and `join_confidence='none'`, written per row so an aggregate can
    always exclude or flag it."""
    build_seq = [i for i, c in enumerate(calls) if c.get("role") == "build"]
    ordered_task_ids = [task_id for task_id, n in zip(task_ids, attempts) for _ in range(n)]
    assigned = dict(zip(build_seq, ordered_task_ids)) if run_join_holds(attempts, len(build_seq)) else {}
    return [
        {**call, "task_id": assigned.get(i), "join_confidence": "heuristic" if i in assigned else "none"}
        for i, call in enumerate(calls)
    ]


def fill_failure_classes(
    calls: Sequence[Mapping[str, Any]], traces_by_path: Mapping[str, Sequence[Mapping[str, Any]]], log_excerpt: str = ""
) -> list[dict[str, Any]]:
    """Sets `failure_class` on every call whose `failure_class` is still unset and
    whose `trace_path` names a key in `traces_by_path`, via the same
    `extract_failure_class` `recovered_call_rows` already uses — 'ok' for a
    successful call, never unset where the trace has a result line. A call with
    no trace, or one `recovered_call_rows` already classified, passes through."""
    return [
        {**call, "failure_class": extract_failure_class(traces_by_path[call["trace_path"]], log_excerpt, call.get("role"))}
        if call.get("failure_class") is None and call.get("trace_path") in traces_by_path
        else dict(call)
        for call in calls
    ]


def _work_store_ticket_done(work_store_root: Path, initiative: Any, phase: Any, ticket: Any) -> bool:
    """True when `work_store_root/initiative/phase/ticket.md`'s frontmatter (parsed
    the way `route.work_item` reads a work item's own `state` field) carries
    `state: done`. False when any of `initiative`/`phase`/`ticket` is missing, or
    the file does not exist — never raises on a work store that doesn't cover
    this task."""
    if not initiative or not phase or not ticket:
        return False
    path = work_store_root / str(initiative) / str(phase) / f"{ticket}.md"
    if not path.exists():
        return False
    fields, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    return fields.get("state") == "done"


def task_row(
    run_id: str,
    phase: str,
    ticket: str,
    record: Mapping[str, Any],
    gate_diffs: Sequence[Mapping[str, Any]],
    log_events: Sequence[Event],
    work_store_root: Path | None = None,
) -> dict[str, Any]:
    """One `tasks` row from one task record, with `gate_diffs` (gathered from the
    run's node records) and `log_events` (parsed from the run's log) folded in so
    `resolve_outcome` sees the full picture and never guesses from a run-level
    budget stop, which names no ticket. `work_store_root`, when given, is checked
    for this ticket's `state: done` ahead of the log line (spec: the work store
    outranks a log line but never the record's own explicit `landed` field)."""
    scoped_ticket = record.get("ticket", ticket)
    work_store_done = work_store_root is not None and _work_store_ticket_done(
        work_store_root, record.get("initiative"), phase, scoped_ticket
    )
    scoped = {
        **record,
        "ticket": scoped_ticket,
        "gate_diffs": gate_diffs,
        "log_events": log_events,
        "work_store_done": work_store_done,
    }
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
    provider_profile_from_ledger: int = 0
    provider_profile_from_node: int = 0
    provider_profile_unresolved: int = 0


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


def _resolve_trace_path(runs_dir: Path, trace: str) -> Path:
    """A call's own `trace` string, resolved against `runs_dir` when it is not
    already absolute — the same base `_read_traces` builds `<run>-trace/` under."""
    path = Path(trace)
    return path if path.is_absolute() else runs_dir / path


def _read_call_traces(runs_dir: Path, usage: Mapping[str, Any] | None, unparsed: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Every distinct trace a run's `usage.json` calls name, parsed once each and
    keyed by the call's own `trace` string — the input `fill_failure_classes` needs
    and `call_rows` itself has no reason to read."""
    traces: dict[str, list[dict[str, Any]]] = {}
    for call in (usage or {}).get("calls") or []:
        trace = call.get("trace")
        if not trace or trace in traces:
            continue
        path = _resolve_trace_path(runs_dir, trace)
        try:
            traces[trace] = load_trace(path)
        except (OSError, UnicodeDecodeError):
            unparsed.append(str(path))
    return traces


def _read_ledger(path: Path, unparsed: list[str]) -> list[dict[str, Any]]:
    """Every JSON object in the autonomy ledger, one per line. A missing file reads
    as no rows (most ingests run without one); a line that is not a JSON object is
    appended to `unparsed` instead of raising."""
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        unparsed.append(str(path))
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            unparsed.append(f"{path}#{line[:40]}")
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _ledger_run_id(row: Mapping[str, Any]) -> str | None:
    """The run_id half of a ledger row's '<run>:<node>' key (stats-join-spike.md:
    'keys rows on <run>:<node>'), or the row's own `run_id`/`run` field when it
    carries one directly instead of a composite key."""
    key = row.get("key")
    if isinstance(key, str) and ":" in key:
        return key.split(":", 1)[0]
    run_id = row.get("run_id") or row.get("run")
    return run_id if isinstance(run_id, str) else None


def provider_profile_for(run_id: str, ledger_rows: Sequence[Mapping[str, Any]]) -> str | None:
    """This run's `provider_profile`: the first ledger row whose key names it, since
    the ledger carries the field on every row (stats-join-spike.md) while the store
    has it nowhere else. None when no row names this run."""
    for row in ledger_rows:
        if _ledger_run_id(row) == run_id:
            profile = row.get("provider_profile")
            if profile:
                return profile
    return None


def provider_profile_from_nodes(node_records: Sequence[Mapping[str, Any]]) -> str | None:
    """This run's `provider_profile` from its own `<run>:<node>.json` records —
    the same key the ledger uses (chair evidence 2026-09-08: 200/200 node records
    carry it). None when no node record names it, the case `provider_profile_source`
    falls back from the ledger into."""
    for node in node_records:
        profile = node.get("provider_profile")
        if profile:
            return profile
    return None


def provider_profile_source(
    run_id: str, ledger_rows: Sequence[Mapping[str, Any]], node_records: Sequence[Mapping[str, Any]]
) -> tuple[str | None, str]:
    """This run's resolved `provider_profile` and which source supplied it: 'ledger'
    when an autonomy-ledger row names it, 'node' when only a `<run>:<node>.json`
    record does, 'none' when neither does. The ledger wins on a conflict, since it is
    the source `provider_profile_for` already trusted."""
    ledger_profile = provider_profile_for(run_id, ledger_rows)
    if ledger_profile:
        return ledger_profile, "ledger"
    node_profile = provider_profile_from_nodes(node_records)
    if node_profile:
        return node_profile, "node"
    return None, "none"


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
    call_traces = _read_call_traces(runs_dir, usage, unparsed) if usage is not None else {}
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
        "call_traces": call_traces,
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


def ingest(
    runs_dir: Path | str,
    db_path: Path | str,
    ledger_path: Path | str | None = None,
    work_store_root: Path | str | None = None,
) -> IngestReport:
    """Upserts every run under `runs_dir` into the stats store at `db_path`, keyed
    by run_id so re-running over the same directory changes nothing. Errors before
    any write when `runs_dir` does not exist, or exists but holds no run records —
    an empty ingest is not a successful one (charter B4). `ledger_path` defaults to
    the autonomy ledger's own path so `provider_profile_source` has rows to read;
    tests pass their own fixture instead. `work_store_root`, like `runs_dir`, is a
    runtime path argument resolved here, never a build-time default: `None` (the
    default) means no task in this ingest can resolve `outcome_source='work_store'`.
    Every run's `provider_profile` is tallied by which source resolved it (ledger,
    node record, or neither); the three counts in the returned report always sum to
    `runs_ingested`."""
    runs_dir = Path(runs_dir)
    work_store_root = Path(work_store_root) if work_store_root is not None else None
    if not runs_dir.is_dir():
        raise FileNotFoundError(f"no such runs directory: {runs_dir.resolve()}")
    run_ids = discover_runs(runs_dir)
    if not run_ids:
        raise FileNotFoundError(f"no run records found under: {runs_dir.resolve()}")

    conn = connect(db_path)
    unparsed: list[str] = []
    ledger_rows = _read_ledger(Path(ledger_path) if ledger_path is not None else LEDGER_PATH, unparsed)
    host = socket.gethostname()
    # profile_sources mirrors `unparsed`'s own accumulation just above: ingest is the
    # edge, already imperative and already writing the database per run (A7), so one
    # more per-run list append here costs nothing a pure core would have avoided.
    profile_sources: list[str] = []
    for run_id in run_ids:
        loaded = load_run(runs_dir, run_id)
        unparsed.extend(loaded["unparsed"])
        gate_diffs = [d for node in loaded["node_records"] for d in (node.get("gate_diffs") or [])]
        log_events = from_log(run_id, loaded["log_lines"])
        profile, profile_source = provider_profile_source(run_id, ledger_rows, loaded["node_records"])
        profile_sources.append(profile_source)
        run = run_row(
            run_id, loaded["usage"], loaded["node_records"], loaded["launched"],
            host=host, provider_profile=profile,
        )
        calls = (
            recovered_call_rows(run_id, loaded["traces"])
            if loaded["usage"] is None and loaded["traces"]
            # "" mirrors recovered_call_rows: extract_failure_class's log_excerpt must be
            # scoped to one call, and load_run has no per-call log slice, only the whole
            # run's — passing that would misclassify every call in a run by any other
            # call's log line (e.g. one call's budget_stop bleeding onto another's).
            else fill_failure_classes(call_rows(run_id, loaded["usage"]), loaded["call_traces"], "")
        )
        tasks = [
            task_row(run_id, phase, ticket, record, gate_diffs, log_events, work_store_root)
            for phase, ticket, record in loaded["task_files"]
        ]
        attempts = [_fix_loop_attempts(record) for _, _, record in loaded["task_files"]]
        joined_calls = assign_task_ids(calls, [t["task_id"] for t in tasks], attempts)
        _upsert(conn, run_id, run, joined_calls, tasks)
    conn.commit()
    conn.close()
    assert len(profile_sources) == len(run_ids)
    return IngestReport(
        runs_ingested=len(run_ids), unparsed_count=len(unparsed), unparsed_sample=tuple(unparsed[:5]),
        provider_profile_from_ledger=profile_sources.count("ledger"),
        provider_profile_from_node=profile_sources.count("node"),
        provider_profile_unresolved=profile_sources.count("none"),
    )
