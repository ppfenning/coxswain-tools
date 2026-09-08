"""Pure aggregation and rendering for the stats store's query surface (spec §5).

Every function takes already-fetched `calls`/`tasks`/`runs` rows as plain
sequences of mappings and returns plain data; opening a sqlite3.Connection and
CLI wiring are out of scope here. `roles_report`, `explain_report` and
`series_report` are the full structured data a `--json` path serializes
directly; `render_capped` is only the default-text path's cap.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from agent_tools.stats_schema import FAILURE_CLASSES

__all__ = ["explain_report", "render_capped", "roles_report", "series_report"]


def _joined(calls: Sequence[Mapping[str, Any]], tasks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Each call plus `task_outcome`/`task_attempt`/`task_cost_usd`, None where `join_confidence` is 'none'/absent or `task_id` names no task."""
    by_id = {t["task_id"]: t for t in tasks if t.get("task_id") is not None}
    rows = []
    for call in calls:
        task = by_id.get(call.get("task_id")) if call.get("join_confidence") not in (None, "none") else None
        rows.append({
            **call,
            "task_outcome": task.get("outcome") if task else None,
            "task_attempt": task.get("attempt") if task else None,
            "task_cost_usd": task.get("cost_usd") if task else None,
        })
    return rows


def _regime_map(runs: Sequence[Mapping[str, Any]]) -> dict[Any, tuple[Any, Any]]:
    """Each run's `(cartridge_sha, provider_profile)` regime, keyed by `run_id`."""
    return {run.get("run_id"): (run.get("cartridge_sha"), run.get("provider_profile")) for run in runs}


def _regime_matches(regime: tuple[Any, Any], cartridge_sha: str | None, provider_profile: str | None) -> bool:
    sha, profile = regime
    return (cartridge_sha is None or sha == cartridge_sha) and (provider_profile is None or profile == provider_profile)


def roles_report(
    calls: Sequence[Mapping[str, Any]],
    tasks: Sequence[Mapping[str, Any]],
    runs: Sequence[Mapping[str, Any]] = (),
    *,
    cartridge_sha: str | None = None,
    provider_profile: str | None = None,
) -> list[dict[str, Any]]:
    """Landed rate, attempts-to-land and $/landed per (role, model), with sample size
    and coverage (spec §5). `runs` supplies each call's `(cartridge_sha,
    provider_profile)` regime by `run_id`; `cartridge_sha`/`provider_profile` narrow
    calls (and their tasks) to runs matching the given value(s) before grouping.
    Every row carries `regimes`, the distinct regimes among its calls' runs — more
    than one means the row averages across a configuration change, and the row says
    so rather than presenting a single silent number."""
    regime_by_run = _regime_map(runs)
    filtering = cartridge_sha is not None or provider_profile is not None
    matching_run_ids = {
        run_id for run_id, regime in regime_by_run.items() if _regime_matches(regime, cartridge_sha, provider_profile)
    }
    matching_calls = [c for c in calls if c.get("run_id") in matching_run_ids] if filtering else calls
    matching_tasks = [t for t in tasks if t.get("run_id") in matching_run_ids] if filtering else tasks

    groups: dict[tuple[Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for row in _joined(matching_calls, matching_tasks):
        groups[(row.get("role"), row.get("model"))].append(row)

    report = []
    for (role, model), rows in groups.items():
        joined = [r for r in rows if r["task_outcome"] is not None]
        joined_task_ids = {r["task_id"] for r in joined}
        landed_task_ids = {r["task_id"] for r in joined if r["task_outcome"] == "landed"}
        landed_rows = [r for r in joined if r["task_outcome"] == "landed"]
        seen_landed: set[Any] = set()
        landed_attempts = []
        attempts_unknown = 0
        landed_cost = 0.0
        landed_costed = 0
        cost_unknown = 0
        for r in landed_rows:
            task_id = r["task_id"]
            if task_id in seen_landed:
                continue
            seen_landed.add(task_id)
            if r["task_attempt"] is not None:
                landed_attempts.append(r["task_attempt"])
            else:
                attempts_unknown += 1
            if r["task_cost_usd"] is not None:
                landed_cost += r["task_cost_usd"]
                landed_costed += 1
            else:
                cost_unknown += 1
        regimes = sorted(
            {regime_by_run[r["run_id"]] for r in rows if r.get("run_id") in regime_by_run},
            key=lambda pair: (pair[0] or "", pair[1] or ""),
        )
        report.append({
            "role": role,
            "model": model,
            "n_calls": len(rows),
            "n_joined": len(joined),
            "landed_rate": round(len(landed_task_ids) / len(joined_task_ids), 4) if joined_task_ids else None,
            "attempts_to_land": round(sum(landed_attempts) / len(landed_attempts), 2) if landed_attempts else None,
            "attempts_unknown": attempts_unknown,
            "cost_per_landed": round(landed_cost / landed_costed, 2) if landed_costed else None,
            "cost_unknown": cost_unknown,
            "coverage": round(len(joined) / len(rows), 4) if rows else 0.0,
            "regimes": [{"cartridge_sha": sha, "provider_profile": profile} for sha, profile in regimes],
        })
    return report


def explain_report(calls: Sequence[Mapping[str, Any]], tasks: Sequence[Mapping[str, Any]], role: str) -> dict[str, Any]:
    """The failure-class breakdown behind one role (spec §5), unclassified calls kept out of the 'unknown' enum bucket."""
    rows = [c for c in calls if c.get("role") == role]
    by_failure_class: dict[str, int] = defaultdict(int)
    unclassified = 0
    for call in rows:
        failure_class = call.get("failure_class")
        if failure_class is None:
            unclassified += 1
        elif failure_class in FAILURE_CLASSES:
            by_failure_class[failure_class] += 1
        else:
            unclassified += 1
    return {
        "role": role,
        "n_calls": len(rows),
        "by_failure_class": dict(by_failure_class),
        "unclassified": unclassified,
        "coverage": round((len(rows) - unclassified) / len(rows), 4) if rows else 0.0,
    }


def series_report(
    runs: Sequence[Mapping[str, Any]],
    tasks: Sequence[Mapping[str, Any]],
    *,
    cartridge_sha: str | None = None,
    provider_profile: str | None = None,
) -> list[dict[str, Any]]:
    """One row per run from the runs/tasks tables, `cost_per_landed` matching
    agent_tools.records.series_row's formula. `cartridge_sha`/`provider_profile`
    narrow the rows to runs matching the given value(s); each row already carries
    its own regime, so no aggregate here ever spans one silently."""
    filtering = cartridge_sha is not None or provider_profile is not None
    matching_runs = (
        [r for r in runs if _regime_matches((r.get("cartridge_sha"), r.get("provider_profile")), cartridge_sha, provider_profile)]
        if filtering else runs
    )
    by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        by_run[task["run_id"]].append(task)

    rows = []
    for run in matching_runs:
        run_id = run["run_id"]
        run_tasks = by_run.get(run_id, [])
        tasks_landed = sum(1 for t in run_tasks if t.get("outcome") == "landed")
        quarantined = sum(1 for t in run_tasks if t.get("outcome") == "quarantined")
        resolved = sum(1 for t in run_tasks if t.get("outcome") not in (None, "unknown"))
        cost_usd = round(sum(float(t.get("cost_usd") or 0.0) for t in run_tasks), 4)
        landed_cost_known = any(t.get("outcome") == "landed" and t.get("cost_usd") is not None for t in run_tasks)
        rows.append({
            "run_id": run_id,
            "cartridge_sha": run.get("cartridge_sha"),
            "provider_profile": run.get("provider_profile"),
            "tasks_landed": tasks_landed,
            "quarantined": quarantined,
            "cost_usd": cost_usd,
            "cost_per_landed": round(cost_usd / tasks_landed, 2) if tasks_landed and landed_cost_known else None,
            "coverage": round(resolved / len(run_tasks), 4) if run_tasks else 0.0,
        })
    return sorted(rows, key=lambda r: r["run_id"])


def render_capped(rows: Sequence[Mapping[str, Any]], cap_tokens: int = 300) -> str:
    """Default text for a report, one line per row plus a summary line, held under `cap_tokens` via a len(text)//4 proxy (no tokenizer in this repo)."""
    lines = [" | ".join(f"{k}={'-' if v is None else v}" for k, v in row.items()) for row in rows]
    summary = f"{len(rows)} rows"

    def render(kept: list[str]) -> str:
        dropped = len(lines) - len(kept)
        marked = kept if not dropped else [*kept, f"... {dropped} more rows"]
        return "\n".join([*marked, summary])

    kept = lines
    while kept and len(render(kept)) // 4 > cap_tokens:
        kept = kept[:-1]
    return render(kept)
