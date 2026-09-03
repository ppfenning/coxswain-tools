"""Read what a harness run recorded: usage files and traces. Pure over parsed data."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

__all__ = ["usage_summary", "trace_summary", "load_usage", "load_trace", "format_table"]


def load_usage(path: Path | str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_trace(path: Path | str) -> list[dict[str, Any]]:
    """Every parseable event in a stream-json trace, in order. Bad lines are skipped, not raised."""
    events = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def usage_summary(usage: Mapping[str, Any]) -> dict[str, Any]:
    """Pure: totals, and cost/turns/calls by role and by model, from a usage record."""
    calls = list(usage.get("calls") or [])
    by_role: dict[str, dict[str, Any]] = defaultdict(lambda: {"calls": 0, "cost_usd": 0.0, "turns": 0})
    by_model: dict[str, dict[str, Any]] = defaultdict(lambda: {"calls": 0, "cost_usd": 0.0, "turns": 0})
    for c in calls:
        for table, key in ((by_role, str(c.get("role"))), (by_model, str(c.get("model")))):
            row = table[key]
            row["calls"] += 1
            row["cost_usd"] = round(row["cost_usd"] + float(c.get("cost_usd") or 0.0), 4)
            row["turns"] += int(c.get("turns") or 0)
    total_in = sum(int(c.get("input_total") if c.get("input_total") is not None else c.get("input_tokens") or 0) for c in calls)
    cached = sum(int(c.get("cache_read_tokens") or 0) for c in calls)
    return {
        "run_id": usage.get("run_id"),
        "calls": len(calls),
        "cost_usd": round(sum(float(c.get("cost_usd") or 0.0) for c in calls), 4),
        "turns": sum(int(c.get("turns") or 0) for c in calls),
        "input_total": total_in,
        "cache_read_share": round(cached / total_in, 3) if total_in else None,
        "by_role": dict(sorted(by_role.items(), key=lambda kv: -kv[1]["cost_usd"])),
        "by_model": dict(sorted(by_model.items(), key=lambda kv: -kv[1]["cost_usd"])),
        "most_expensive": sorted(
            ({"role": c.get("role"), "model": c.get("model"), "turns": c.get("turns"), "cost_usd": c.get("cost_usd")} for c in calls),
            key=lambda r: -float(r["cost_usd"] or 0),
        )[:5],
    }


def trace_summary(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Pure: what a traced node did — tool calls by name, reads by file, commands run, and its result."""
    tools: Counter[str] = Counter()
    reads: Counter[str] = Counter()
    whole_reads = 0
    commands: list[str] = []
    result: Mapping[str, Any] | None = None
    for e in events:
        if e.get("type") == "assistant":
            for block in (e.get("message") or {}).get("content") or []:
                if block.get("type") != "tool_use":
                    continue
                name = str(block.get("name"))
                tools[name] += 1
                inp = block.get("input") or {}
                if name == "Read":
                    reads[str(inp.get("file_path", "?")).rsplit("/", 1)[-1]] += 1
                    if not inp.get("limit"):
                        whole_reads += 1
                if name == "Bash":
                    commands.append(str(inp.get("command", ""))[:80])
        elif e.get("type") == "result":
            result = e
    return {
        "turns": (result or {}).get("num_turns"),
        "cost_usd": (result or {}).get("total_cost_usd"),
        "subtype": (result or {}).get("subtype"),
        "is_error": bool((result or {}).get("is_error")),
        "tools": dict(tools.most_common()),
        "reads": dict(reads.most_common(10)),
        "whole_file_reads": whole_reads,
        "commands": commands,
    }


def format_table(rows: Iterable[Mapping[str, Any]], columns: Sequence[str]) -> str:
    """Pure: a plain text table, columns as given, numbers right-aligned."""
    rows = list(rows)
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) if rows else len(c) for c in columns}
    def cell(r: Mapping[str, Any], c: str) -> str:
        v = r.get(c, "")
        s = f"{v:.2f}" if isinstance(v, float) else str(v)
        return s.rjust(widths[c]) if isinstance(v, (int, float)) else s.ljust(widths[c])
    head = "  ".join(c.ljust(widths[c]) for c in columns)
    return "\n".join([head, *("  ".join(cell(r, c) for c in columns) for r in rows)])
