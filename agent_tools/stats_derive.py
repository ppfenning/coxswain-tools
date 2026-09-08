"""Pure derivation functions for the stats store's ingest task.

Spec: docs/design/run-stats-store.md §3. Spike verdict:
agent_tools/stats-join-spike.md. Each function here takes already-parsed
plain data — no file reads, no network, no clock — and returns plain data;
the ingest task (out of scope here) is responsible for reading
*.usage.json, task-record json, the autonomy ledger and run logs, and for
parsing raw log text into agent_tools.events.Event objects before calling
resolve_outcome.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import reduce
from typing import Any

from agent_tools.stats_schema import FAILURE_CLASSES, OUTCOMES

__all__ = ["attempt_numbers", "extract_failure_class", "resolve_outcome"]


def attempt_numbers(calls: Sequence[Mapping[str, Any]]) -> list[int]:
    """Each call's 1-based ordinal among calls sharing its 'role', in call order."""

    def step(state: tuple[dict[str, int], list[int]], call: Mapping[str, Any]) -> tuple[dict[str, int], list[int]]:
        counts, attempts = state
        role = call["role"]
        n = counts.get(role, 0) + 1
        return {**counts, role: n}, [*attempts, n]

    _, attempts = reduce(step, calls, ({}, []))
    return attempts


def resolve_outcome(task_record: Mapping[str, Any]) -> tuple[str, str]:
    """(outcome, outcome_source) per spec §3's order: landed, then the last
    gate_diffs entry targeting this ticket, then the work store's own
    `state: done`, then a log line naming this ticket, else unknown.

    A run-level `budget_stop` log event (agent_tools/events.py:51) carries an
    empty detail dict and names no task: a run holds several tickets, so there
    is no sound way to attribute it to one of them. It is therefore never a
    source of a task's outcome here; a task that only budget-stop's run has
    `unknown`, not a guess.
    """
    if task_record.get("landed") is True:
        return "landed", "landed_field"

    ticket = task_record.get("ticket")
    gate_diffs = task_record.get("gate_diffs") or ()
    scoped = [d for d in gate_diffs if ticket is not None and d.get("target") == ticket]
    if scoped:
        outcome = scoped[-1]["outcome"]
        if outcome in OUTCOMES:
            return outcome, "gate_diffs"

    if task_record.get("work_store_done") is True:
        return "landed", "work_store"

    log_events = task_record.get("log_events") or ()
    if ticket is not None and any(
        e.kind == "task_quarantined" and e.detail.get("task") == ticket for e in log_events
    ):
        return "quarantined", "log_line"

    return "unknown", "unknown"


_EDIT_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})

_PATCHING_ROLES = frozenset({"build", "style_pass"})


def _final_result(trace: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """The last `type: result` event, whose `subtype`/`is_error`/`num_turns`/
    `total_cost_usd` sit at its own top level — the same shape
    agent_tools.records.trace_summary reads. Never `{}`'s a `result` sub-key:
    the per-call trace carries no such nesting."""
    result: Mapping[str, Any] | None = None
    for entry in trace:
        if entry.get("type") == "result":
            result = entry
    return result or {}


def _content_blocks(entry: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return list((entry.get("message") or {}).get("content") or [])


def _budget_stopped(trace: Sequence[Mapping[str, Any]], log_excerpt: str) -> bool:
    if "error_max_budget_usd" in log_excerpt or "fix loop stopped: budget" in log_excerpt:
        return True
    return _final_result(trace).get("subtype") == "error_max_budget_usd"


def _tool_errored(trace: Sequence[Mapping[str, Any]]) -> bool:
    return any(
        block.get("type") == "tool_result" and block.get("is_error")
        for entry in trace
        if entry.get("type") == "user"
        for block in _content_blocks(entry)
    )


def _refused(trace: Sequence[Mapping[str, Any]]) -> bool:
    return any(
        entry.get("type") == "assistant" and (entry.get("message") or {}).get("stop_reason") == "refusal"
        for entry in trace
    )


def _touched_no_files(trace: Sequence[Mapping[str, Any]]) -> bool:
    return not any(
        block.get("type") == "tool_use" and block.get("name") in _EDIT_TOOLS
        for entry in trace
        if entry.get("type") == "assistant"
        for block in _content_blocks(entry)
    )


def _succeeded(trace: Sequence[Mapping[str, Any]]) -> bool:
    result = _final_result(trace)
    return bool(result) and not result.get("is_error")


def extract_failure_class(trace: Sequence[Mapping[str, Any]], log_excerpt: str, role: str | None) -> str:
    """One of stats_schema.FAILURE_CLASSES. Trusts only the trace's terminal
    `type: result` entry to decide success, matching the convention
    agent_tools/records.py:102 already uses (`is_error` read from the last
    result only) — a call that hits a failed tool_result or a refusal partway
    through and then finishes clean is `ok`, not `tool_error` or `refused`.
    Assumes trace and log_excerpt are already scoped to this one call by the
    caller; scoping them is the ingest task's job, not enforced here.

    `empty_patch` is only meaningful where a patch was expected: `role` outside
    `_PATCHING_ROLES` (only `build` and `style_pass` are granted Write/Edit by
    the provider profile) can never read `empty_patch` on success, only `ok`."""
    if _succeeded(trace):
        candidate = "empty_patch" if role in _PATCHING_ROLES and _touched_no_files(trace) else "ok"
    elif _budget_stopped(trace, log_excerpt):
        candidate = "budget_stop"
    elif _tool_errored(trace):
        candidate = "tool_error"
    elif _refused(trace):
        candidate = "refused"
    else:
        candidate = "unknown"
    return candidate if candidate in FAILURE_CLASSES else "unknown"
