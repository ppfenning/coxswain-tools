"""Read what a harness run recorded: usage files and traces. Pure over parsed data."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

__all__ = ["usage_summary", "trace_summary", "load_usage", "load_trace", "format_table",
           "series_row", "series", "series_totals", "series_new_lines"]


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


def _earliest_manifest(manifests: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Pure: the manifest a series row's date/sha/profile come from, chosen without regard to
    glob order. A manifest carrying `ts` always outranks one that does not; ties (including
    every undated manifest) break on the manifest's own sorted-key JSON, not on list position."""
    if not manifests:
        return None
    return sorted(manifests, key=lambda m: (0 if m.get("ts") else 1, str(m.get("ts") or ""), json.dumps(m, sort_keys=True)))[0]


def _usage_figures(usage: Mapping[str, Any] | None) -> dict[str, Any]:
    """Pure: calls/turns/cost_usd/cache_share from a usage record, whether or not it carries a
    precomputed `summary` — derived through usage_summary() when `calls` is present so a usage
    file with only `calls` (the repository's own fixture shape) and one with a matching
    precomputed `summary` give the same figures."""
    if not usage:
        return {"calls": 0, "turns": 0, "cost_usd": 0.0, "cache_share": 0.0}
    if usage.get("calls"):
        s = usage_summary(usage)
        return {"calls": s["calls"], "turns": s["turns"], "cost_usd": s["cost_usd"],
                "cache_share": round(s.get("cache_read_share") or 0.0, 2)}
    summary = usage.get("summary") or {}
    cost_usd = round(float(summary.get("cost_usd") or 0.0), 4)
    input_total = int(summary.get("input_total") or 0)
    cache_read_tokens = int(summary.get("cache_read_tokens") or 0)
    cache_share = round(cache_read_tokens / input_total, 2) if input_total else 0.0
    return {"calls": int(summary.get("calls") or 0), "turns": int(summary.get("turns") or 0),
            "cost_usd": cost_usd, "cache_share": cache_share}


def series_row(run_id: str, usage: Mapping[str, Any] | None, manifests: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Pure: one steward-pass row for a run, from its usage summary and its phase manifests."""
    calls_list = list((usage or {}).get("calls") or [])
    figures = _usage_figures(usage)
    earliest = _earliest_manifest(manifests)
    date = earliest.get("ts", "")[:10] if earliest and earliest.get("ts") else ""
    cartridge_sha = (earliest.get("cartridge_sha") or "")[:12] if earliest else ""
    provider_profile = earliest.get("provider_profile") or "" if earliest else ""
    tasks_landed = sum(int((m.get("totals") or {}).get("completed") or 0) for m in manifests)
    quarantined = sum(int((m.get("totals") or {}).get("quarantined") or 0) for m in manifests)
    review_calls = sum(1 for c in calls_list if str(c.get("role") or "").startswith("review") or c.get("role") == "arbitrate")
    return {
        "run": run_id,
        "date": date,
        "cartridge_sha": cartridge_sha,
        "provider_profile": provider_profile,
        "calls": figures["calls"],
        "turns": figures["turns"],
        "cost_usd": figures["cost_usd"],
        "cache_share": figures["cache_share"],
        "tasks_landed": tasks_landed,
        "quarantined": quarantined,
        "review_rounds": round(review_calls / tasks_landed, 2) if tasks_landed else 0.0,
        "cost_per_landed": round(figures["cost_usd"] / tasks_landed, 2) if tasks_landed else None,
    }


def series(files: Mapping[str, str]) -> list[dict[str, Any]]:
    """Pure: one row per run, grouping a runs-dir's usage files and phase manifests by run id.
    A file that fails to parse, or parses to something other than a mapping, is skipped, not raised —
    same contract as load_trace's bad lines."""
    by_run: dict[str, dict[str, Any]] = defaultdict(lambda: {"usage": None, "manifests": []})
    for name, text in files.items():
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        if name.endswith(".usage.json"):
            by_run[name[: -len(".usage.json")]]["usage"] = parsed
        elif ":" in name:
            by_run[name.split(":", 1)[0]]["manifests"].append(parsed)
    rows = [series_row(run_id, entry["usage"], entry["manifests"]) for run_id, entry in by_run.items()]
    return sorted(rows, key=lambda r: (r["date"], r["run"]))


def series_totals(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Pure: totals across a series() result."""
    cost_usd = round(sum(float(r.get("cost_usd") or 0.0) for r in rows), 4)
    tasks_landed = sum(int(r.get("tasks_landed") or 0) for r in rows)
    return {
        "runs": len(rows),
        "cost_usd": cost_usd,
        "tasks_landed": tasks_landed,
        "quarantined": sum(int(r.get("quarantined") or 0) for r in rows),
        "cost_per_landed": round(cost_usd / tasks_landed, 2) if tasks_landed else None,
        "runs_landing_nothing": sum(1 for r in rows if not r.get("tasks_landed")),
    }


def series_new_lines(existing: str, rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Pure: one JSON line per row whose run id is not already present in an append file's JSONL text.
    A malformed or non-mapping line in `existing` is skipped, not raised, when reading what is already there."""
    present: set[Any] = set()
    for line in existing.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "run" in obj:
            present.add(obj["run"])
    return [json.dumps(r) for r in rows if r.get("run") not in present]


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
