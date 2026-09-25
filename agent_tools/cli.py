"""cox — the coxswain's operator tools (alias: agent-tools, removed next release)."""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime
import importlib.metadata
import io
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
import uuid
from pathlib import Path

import yaml

from agent_tools import (
    chair,
    cleanup,
    commands,
    commands_render,
    courier,
    doctor,
    epic,
    install,
    install_exec,
    land,
    leader_chat,
    notify,
    pacing,
    plan,
    provenance,
    records,
    release,
    release_check,
    release_check_cli,
    release_check_index,
    release_check_manifest,
    release_check_notes,
    release_check_pages,
    release_check_readmes,
    route,
    route_sync,
    route_sync_gh,
    router,
    runs_bar,
    runs_detail,
    runs_detail_screen,
    runs_stranded,
    runs_top,
    runs_top_screen,
    schema,
    setup_install,
    setup_screen,
    stats_ingest,
    stats_query,
    stats_schema,
    steward,
    usage_window,
)
from agent_tools import runs as runs_module


def _runs_usage(a: argparse.Namespace) -> int:
    runs_dir = Path(a.runs_dir)
    path = runs_dir / f"{a.run_id}.usage.json"
    header = None
    if path.exists():
        s = records.usage_summary(records.load_usage(path))
    else:
        pid_text = _read_text_or_none(runs_dir / f"{a.run_id}.pid")
        pid = route.parse_pid(pid_text) if pid_text is not None else None
        live = epic.run_live(pid, runs_dir / f"{a.run_id}.pid")
        trace_dir = runs_dir / f"{a.run_id}-trace"
        traces = stats_ingest._read_traces(trace_dir, [])
        if not live and not traces:
            print(f"no usage record and no trace for {a.run_id} in {runs_dir}")
            return 2
        rows = stats_ingest.recovered_call_rows(a.run_id, traces)
        s = records.usage_summary({"run_id": a.run_id, "calls": rows})
        header = f"live (pid {pid}) — from the trace so far" if live else "not live — from the trace so far"
    if a.json:
        print(json.dumps({**s, "note": header} if header else s, indent=2)); return 0
    if header:
        print(header)
    print(f"{s['run_id']}: {s['calls']} calls, {s['turns']} turns, ${s['cost_usd']:.2f}, cache-read share {s['cache_read_share']}")
    print(records.format_table([{"role": k, **v} for k, v in s["by_role"].items()], ["role", "calls", "cost_usd", "turns"]))
    print(); print(records.format_table([{"model": k, **v} for k, v in s["by_model"].items()], ["model", "calls", "cost_usd", "turns"]))
    return 0


_SERIES_COLUMNS = ["run", "date", "cartridge_sha", "provider_profile", "calls", "turns", "cost_usd",
                   "cache_share", "tasks_landed", "quarantined", "review_rounds", "cost_per_landed",
                   "tier_ceiling", "effort_ceiling", "launched_by"]


def _runs_series(a: argparse.Namespace) -> int:
    d = Path(a.runs_dir)
    files = {f.name: f.read_text(encoding="utf-8")
             for pattern in ("*.usage.json", "*:*.json", "*.ceiling.json", "*.launched.json") for f in d.glob(pattern)}
    rows = records.series(files)
    totals = records.series_totals(rows)
    if a.json:
        print(json.dumps({"rows": rows, "totals": totals}, indent=2))
    else:
        print(records.format_table(rows, _SERIES_COLUMNS))
        print(f"totals: {totals['runs']} runs, ${totals['cost_usd']:.2f}, {totals['tasks_landed']} landed, "
              f"{totals['quarantined']} quarantined, cost/landed {totals['cost_per_landed']}, "
              f"{totals['runs_landing_nothing']} landed nothing")
    if a.append:
        p = Path(a.append)
        new_lines = records.series_new_lines(p.read_text(encoding="utf-8") if p.exists() else "", rows)
        if new_lines:
            with p.open("a", encoding="utf-8") as fh:
                fh.write("".join(line + "\n" for line in new_lines))
    return 0


def _runs_events(a: argparse.Namespace) -> int:
    for ev in runs_module.events(Path(a.runs_dir), follow=a.follow):
        if a.json:
            print(json.dumps(dataclasses.asdict(ev)), flush=True)
        else:
            detail = " ".join(f"{k}={v}" for k, v in ev.detail.items())
            print(f"{ev.run} {ev.kind} {detail}".rstrip(), flush=True)
    return 0


def _stats_ingest(a: argparse.Namespace) -> int:
    try:
        report = stats_ingest.ingest(
            a.runs_dir, a.db, work_store_root=a.work_store_root, cartridges_repo=a.cartridges_repo,
        )
    except FileNotFoundError as exc:
        print(exc)
        return 1
    profile_summary = (
        f"provider_profile: {report.provider_profile_from_ledger} from ledger, "
        f"{report.provider_profile_from_node} from node record, "
        f"{report.provider_profile_unresolved} unresolved; "
        f"provider_profile_sha: {report.provider_profile_sha_resolved} resolved, "
        f"{report.provider_profile_sha_unresolved} unresolved"
    )
    if report.unparsed_count:
        print(f"{report.runs_ingested} run(s) ingested; {report.unparsed_count} file(s) failed to parse:")
        for path in report.unparsed_sample:
            print(f"  {path}")
        return 1
    print(f"{report.runs_ingested} run(s) ingested from {a.runs_dir} into {a.db} ({profile_summary})")
    return 0


def _stats_fetch(conn: sqlite3.Connection, table: str) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall()]


def _stats_roles(a: argparse.Namespace) -> int:
    conn = stats_schema.connect(a.db)
    try:
        report = stats_query.roles_report(
            _stats_fetch(conn, "calls"), _stats_fetch(conn, "tasks"), _stats_fetch(conn, "runs"),
            cartridge_sha=a.cartridge_sha, provider_profile=a.provider_profile,
        )
    finally:
        conn.close()
    print(json.dumps(report, indent=2) if a.json else stats_query.render_capped(report))
    return 0


def _stats_coverage(a: argparse.Namespace) -> int:
    conn = stats_schema.connect(a.db)
    try:
        report = stats_query.coverage_report(
            _stats_fetch(conn, "calls"), _stats_fetch(conn, "tasks"), _stats_fetch(conn, "runs"),
        )
    finally:
        conn.close()
    print(json.dumps(report, indent=2) if a.json else stats_query.render_capped(report))
    return 0


def _stats_explain(a: argparse.Namespace) -> int:
    conn = stats_schema.connect(a.db)
    try:
        report = stats_query.explain_report(_stats_fetch(conn, "calls"), _stats_fetch(conn, "tasks"), a.role)
    finally:
        conn.close()
    print(json.dumps(report, indent=2) if a.json else stats_query.render_capped([report]))
    return 0


def _stats_series(a: argparse.Namespace) -> int:
    conn = stats_schema.connect(a.db)
    try:
        report = stats_query.series_report(
            _stats_fetch(conn, "runs"), _stats_fetch(conn, "tasks"),
            cartridge_sha=a.cartridge_sha, provider_profile=a.provider_profile,
        )
    finally:
        conn.close()
    print(json.dumps(report, indent=2) if a.json else stats_query.render_capped(report))
    return 0


def _stats_spend_mix(a: argparse.Namespace) -> int:
    conn = stats_schema.connect(a.db)
    try:
        report = stats_query.spend_mix_report(_stats_fetch(conn, "calls"))
    finally:
        conn.close()
    print(json.dumps(report, indent=2) if a.json else stats_query.render_capped(report))
    return 0


def _bounds_ceiling_for(a: argparse.Namespace):
    """role -> declared ceiling from the resolved provider profile's
    `role_budget_usd` (per-role) falling back to its `budget_usd` (default);
    `None` for both when the routing profile at `_profile_path(a)` (spec §1:
    `--profile`, `$AGENT_TOOLS_PROFILE` or `DEFAULT_PROFILE`, same as `route
    context`/`setup doctor`) or its named `provider_profile` is missing or
    unreadable — reported, never refused, matching `_gather_doctor_facts`'s
    style rather than `_resolve_profile_or_refuse`'s (docs/design/
    cost-bounds.md §2: 'the declared ceiling from the resolved provider
    profile')."""
    text = _read_text_or_none(_profile_path(a))
    try:
        routing_profile = route.parse_profile(text) if text is not None else {}
    except route.ProfileError:
        routing_profile = {}
    provider_path = routing_profile.get("provider_profile")
    provider_text = _read_text_or_none(Path(provider_path).expanduser()) if provider_path else None
    try:
        provider_profile = yaml.safe_load(provider_text) if provider_text is not None else None
    except yaml.YAMLError:
        provider_profile = None
    provider_profile = provider_profile if isinstance(provider_profile, dict) else {}
    role_budget_usd = provider_profile.get("role_budget_usd") or {}
    budget_usd = provider_profile.get("budget_usd")
    budget_usd = budget_usd if isinstance(budget_usd, dict) else {}
    tier_overrides = provider_profile.get("tier_overrides") or {}
    defaults = provider_profile.get("defaults") or {}

    def _ceiling(role, _model):
        tier = _resolved_tier(tier_overrides, defaults, role)
        value = role_budget_usd[role] if role in role_budget_usd else budget_usd.get(tier)
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    return _ceiling


def _resolved_tier(tier_overrides: dict, defaults: dict, role: str) -> str:
    """A role's tier: its own `tier_overrides` entry, else the profile's
    `defaults` entry, else `"standard"` — the chain `_bounds_ceiling_for`
    and `_router_policy_for` both resolve a role's tier through."""
    return tier_overrides.get(role, defaults.get(role, "standard"))


def _stats_bounds(a: argparse.Namespace) -> int:
    conn = stats_schema.connect(a.db)
    try:
        report = stats_query.bounds_report(_stats_fetch(conn, "calls"), _bounds_ceiling_for(a))
    finally:
        conn.close()
    if a.level:
        drop = {"strict", "moderate", "liberal"} - {a.level}
        report = [{k: v for k, v in row.items() if k not in drop} for row in report]
    if a.write:
        write_path = Path(a.write).resolve()
        repo_root = Path.cwd().resolve()
        if write_path != repo_root and repo_root not in write_path.parents:
            print(f"stats bounds: --write path {write_path} is outside the repo checkout {repo_root}")
            return 2
        generated = datetime.datetime.now(datetime.UTC).isoformat()
        write_path.write_text(json.dumps({"generated": generated, "db": a.db, "rows": report}, indent=2))
    print(json.dumps(report, indent=2) if a.json else stats_query.render_capped(report))
    return 0


def _router_policy_for(a: argparse.Namespace, role: str) -> dict | None:
    """Routing inputs for `role`: `policy["mode"]` is the routing profile's
    own `router` key (off|shadow|on), defaulting to `"off"` when absent
    since router-steward.md §1 names the three values but not a default.
    `policy["default_tier"]` resolves the same chain `_bounds_ceiling_for`
    resolves a role's tier through: `tier_overrides`, then `defaults`, then
    `"standard"`. `None` when the routing profile at `_profile_path(a)` or
    its `provider_profile` is missing or unparseable, so the caller can
    refuse rather than invent a policy."""
    text = _read_text_or_none(_profile_path(a))
    try:
        routing_profile = route.parse_profile(text) if text is not None else None
    except route.ProfileError:
        routing_profile = None
    if routing_profile is None:
        return None
    provider_path = routing_profile.get("provider_profile")
    provider_text = _read_text_or_none(Path(provider_path).expanduser()) if provider_path else None
    try:
        provider_profile = yaml.safe_load(provider_text) if provider_text is not None else None
    except yaml.YAMLError:
        provider_profile = None
    if not isinstance(provider_profile, dict):
        return None
    tier_overrides = provider_profile.get("tier_overrides") or {}
    defaults = provider_profile.get("defaults") or {}
    return {
        "mode": routing_profile.get("router", "off"),
        "default_tier": _resolved_tier(tier_overrides, defaults, role),
        "min_n": 20,
        "deviation_floor": 0.70,
        "challenger_n": 10,
    }


def _stats_window_from_report(report: list[dict], role: str) -> dict:
    """`stats_window` for `role`, sliced from `stats_query.roles_report`'s
    existing output rather than a new aggregation: `n` sums `n_calls`
    across every row for the role, challenger rows included
    (router-steward.md §1: "n counts every call of that role in the
    window, challenger calls included, so the section 2 schedule lands on
    every Nth call"). `landed_rate` is the role's non-challenger rows'
    `landed_rate` weighted by `n_calls`, 0.0 when none carry one
    (router-steward.md §3 excludes challenger rows from that aggregate).
    `start`/`end` are `None`: no `roles_report` row carries a window
    timestamp, and `select_tier` never reads either field."""
    rows = [row for row in report if row["role"] == role]
    n = sum(row["n_calls"] for row in rows)
    floor_rows = [row for row in rows if not row["challenger"]]
    rated = [row for row in floor_rows if row["landed_rate"] is not None]
    weight = sum(row["n_calls"] for row in rated)
    landed_rate = sum(row["landed_rate"] * row["n_calls"] for row in rated) / weight if weight else 0.0
    return {"landed_rate": landed_rate, "n": n, "start": None, "end": None}


def _router_stats_window_for(a: argparse.Namespace, role: str) -> dict:
    """The edge for `_stats_window_from_report`: opens `a.db` and fetches
    the same `roles_report` inputs `cox stats roles` reads, no new
    aggregation added to `stats_query.py`."""
    conn = stats_schema.connect(a.db)
    try:
        report = stats_query.roles_report(_stats_fetch(conn, "calls"), _stats_fetch(conn, "tasks"), _stats_fetch(conn, "runs"))
    finally:
        conn.close()
    return _stats_window_from_report(report, role)


def _router_select(a: argparse.Namespace) -> int:
    """`cox router select` — the CLI edge of docs/design/router-steward.md:
    `router.select_tier` stays pure; every read happens here. `off` returns
    the floor without calling `select_tier`. `shadow` calls it, prints the
    would-be decision marked as not authoritative, and still returns the
    floor as the effective answer. `on` calls it and returns its tier as
    the effective, authoritative answer."""
    policy = _router_policy_for(a, a.role)
    if policy is None:
        print(f"router select: no usable profile at {_profile_path(a)}")
        return 2
    mode = policy["mode"]
    floor = policy["default_tier"]
    if mode == "off":
        effective, reason = floor, "off"
    else:
        stats_window = _router_stats_window_for(a, a.role)
        tier, tier_reason = router.select_tier(a.role, stats_window, policy)
        if mode == "shadow":
            print(f"router select: shadow tier={tier} reason={tier_reason} (not authoritative; effective stays {floor})")
            effective, reason = floor, "shadow"
        else:
            effective, reason = tier, tier_reason
    if a.json:
        print(json.dumps({"role": a.role, "mode": mode, "effective_tier": effective, "reason": reason}))
    else:
        print(f"router select: role={a.role} mode={mode} effective_tier={effective} reason={reason}")
    return 0


def _steward_recent_run_ids(pair_calls: list[dict], run_span: dict, *, day_cap: int, run_cap: int) -> set:
    """Run ids for `pair_calls`, newest-first by `ended_at` (falling back to
    `started_at`), kept up to `run_cap` runs or until a run's `started_at`
    falls more than `day_cap` days before the newest kept run's own end —
    whichever limit is hit first (router-steward.md §5: "a window capped at
    14 days or 20 runs, whichever completes first"). Measured from the
    pair's own latest run, never the wall clock, so a pair that has simply
    existed a long time is not disqualified by history it no longer needs."""
    ordered = sorted(
        {c["run_id"] for c in pair_calls if c.get("run_id") in run_span},
        key=lambda rid: run_span[rid][1] or run_span[rid][0] or "",
        reverse=True,
    )
    if not ordered:
        return set()
    newest_end = run_span[ordered[0]][1] or run_span[ordered[0]][0]
    cutoff = datetime.datetime.fromisoformat(newest_end) - datetime.timedelta(days=day_cap) if newest_end else None
    kept = []
    for rid in ordered:
        if len(kept) >= run_cap:
            break
        started_at = run_span[rid][0]
        if cutoff is not None and started_at is not None and datetime.datetime.fromisoformat(started_at) < cutoff:
            break
        kept.append(rid)
    return set(kept)


def _steward_window_days_for(run_ids: set, run_span: dict) -> int:
    """Days between the earliest `started_at` and the latest `ended_at`
    among `run_ids`; `0` when neither parses."""
    starts = [run_span[rid][0] for rid in run_ids if run_span.get(rid, (None, None))[0]]
    ends = [run_span[rid][1] for rid in run_ids if run_span.get(rid, (None, None))[1]]
    if not starts or not ends:
        return 0
    start = datetime.datetime.fromisoformat(min(starts))
    end = datetime.datetime.fromisoformat(max(ends))
    return (end - start).days


def _steward_candidate_rows(bounds: list[dict], calls: list[dict], tasks: list[dict], runs: list[dict]) -> list[dict]:
    """Per (role, model) in `bounds`: `stats_query.roles_report`'s
    challenger/floor split and `n_calls`, recomputed over only that pair's
    own most recent 20 runs or 14 days (`_steward_recent_run_ids`) rather
    than its whole history, joined onto `bound`'s ceiling/censored/strict/
    moderate/liberal. A pair with no runs in that window, no challenger
    row, no floor row, or either row's `landed_rate` is dropped: the
    evidence bar cannot score a delta it cannot compute. Pure: no file, no
    db connection, no clock read — `calls`/`tasks`/`runs` arrive already
    fetched."""
    run_span = {r["run_id"]: (r.get("started_at"), r.get("ended_at")) for r in runs}
    rows = []
    for bound in bounds:
        role, model = bound["role"], bound["model"]
        pair_calls = [c for c in calls if c.get("role") == role and c.get("model") == model]
        recent_run_ids = _steward_recent_run_ids(pair_calls, run_span, day_cap=14, run_cap=20)
        if not recent_run_ids:
            continue
        recent_calls = [c for c in pair_calls if c.get("run_id") in recent_run_ids]
        recent_tasks = [t for t in tasks if t.get("run_id") in recent_run_ids]
        windowed = stats_query.roles_report(recent_calls, recent_tasks, runs)
        challenger_row = next((r for r in windowed if r["role"] == role and r["model"] == model and r["challenger"]), None)
        floor_row = next((r for r in windowed if r["role"] == role and r["model"] == model and not r["challenger"]), None)
        if challenger_row is None or floor_row is None:
            continue
        if challenger_row["landed_rate"] is None or floor_row["landed_rate"] is None:
            continue
        rows.append({
            **bound,
            "n_challenger": challenger_row["n_calls"],
            "landed_rate_challenger": challenger_row["landed_rate"],
            "landed_rate_floor": floor_row["landed_rate"],
            "window_days": _steward_window_days_for(recent_run_ids, run_span),
        })
    return rows


def _steward_bounds_rows_for(a: argparse.Namespace) -> list[dict]:
    """Edge for `_steward_candidate_rows`: opens `a.db` once, fetches
    `calls`/`tasks`/`runs` and the read-only ceiling from
    `_bounds_ceiling_for`, then hands everything to the pure join. Writes
    nothing."""
    conn = stats_schema.connect(a.db)
    try:
        calls = _stats_fetch(conn, "calls")
        tasks = _stats_fetch(conn, "tasks")
        runs = _stats_fetch(conn, "runs")
    finally:
        conn.close()
    bounds = stats_query.bounds_report(calls, _bounds_ceiling_for(a))
    return _steward_candidate_rows(bounds, calls, tasks, runs)


def _steward_propose(a: argparse.Namespace) -> int:
    """`cox steward propose` — the CLI edge of docs/design/router-steward.md
    §4: reads `a.db` and the routing/provider profile read-only to build
    candidate rows, then writes each candidate that clears §5's bar as a
    new intake file via the same writer `route file --intake` uses. It
    never opens a provider profile to write it; its only writes are new
    files under `workspace_dir/intake`."""
    profile, rc = _resolve_profile_or_refuse(a)
    if rc is not None:
        return rc
    ws = Path(profile["workspace_dir"]).expanduser()
    policy = {"min_n": 20, "window_days_cap": 14, "landed_rate_delta_floor": 0.05}
    candidates = steward.ceiling_candidates(_steward_bounds_rows_for(a), policy)
    if not candidates:
        if a.json:
            print(json.dumps([]))
        else:
            print("steward propose: [] (no candidate cleared the evidence bar)")
        return 0
    date = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
    mapping: dict[str, str] = {}
    for candidate in candidates:
        title = f"steward: {candidate['direction']} {candidate['role']}'s ceiling for {candidate['model']}"
        mapping.update(route.intake_file(title, steward.render_proposal(candidate), "coxswain-tools", date))
    targets = {rel: ws / rel for rel in mapping}
    existing = [str(path) for path in targets.values() if path.exists()]
    if existing:
        print(f"routing: refusing to overwrite existing path(s): {', '.join(existing)}")
        return 2
    for rel, path in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(mapping[rel], encoding="utf-8")
    written = [str(targets[rel]) for rel in sorted(mapping)]
    print(json.dumps(written) if a.json else "\n".join(written))
    return 0


def _runs_top(a: argparse.Namespace) -> int:
    heartbeat_minutes = _leader_heartbeat_minutes()
    if a.once:
        chair_state = runs_top_screen.chair_now(a.runs_dir, heartbeat_minutes)
        print("\n".join(runs_top.render(runs_top_screen.rows_now(a.runs_dir, heartbeat_minutes), 120, chair_state)))
        return 0
    if not sys.stdin.isatty():
        print("runs top: needs a terminal; use --once")
        return 2
    return runs_top_screen.main(a.runs_dir, a.interval, heartbeat_minutes)


def _runs_bar(a: argparse.Namespace) -> int:
    rows = [
        dataclasses.asdict(runs_top.row(f["run"], f["alive"], f["phases"], f["events"], f["calls"], f["ceiling"], f["launched_by"]))
        for f in runs_top_screen.facts(a.runs_dir)
    ]
    print(json.dumps(runs_bar.bar(rows, runs_bar.attention(rows))))
    return 0


def _resolved_notify_policy(runs_dir: Path) -> dict:
    """`policy.notify` from `<runs_dir>/policy.notify.json` when present, else DEFAULT_POLICY, field by field."""
    default = notify.DEFAULT_POLICY
    text = _read_text_or_none(runs_dir / "policy.notify.json")
    if text is None:
        return default
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return default
    if not isinstance(raw, dict):
        return default
    return {"kinds": raw.get("kinds", default["kinds"]), "min_cost_usd": raw.get("min_cost_usd", default["min_cost_usd"])}


def _runs_notify(a: argparse.Namespace) -> int:
    policy = _resolved_notify_policy(Path(a.runs_dir))
    return notify.run_loop(a.runs_dir, once=a.once, interval=a.interval, policy=policy,
                            heartbeat_minutes=_leader_heartbeat_minutes(), replay=a.replay)


def _runs_detail(a: argparse.Namespace) -> int:
    d = runs_detail.detail(**runs_detail_screen.facts_for(a.runs_dir, a.run_id))
    if a.json:
        print(json.dumps(dataclasses.asdict(d)))
    else:
        print("\n".join(runs_detail.render(d, 120)))
    return 0


def _runs_stranded(a: argparse.Namespace) -> int:
    runs_dir, reason = _runs_dir_for_land(a)
    if runs_dir is None:
        print(f"stranded: {reason}")
        return 2
    task_records = []
    for path in sorted(runs_dir.glob("*/tasks/*/*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        record.setdefault("run", path.parent.parent.parent.name)
        record.setdefault("task", path.stem)
        record.setdefault("phase", path.parent.name)
        task_records.append(record)
    ws = runs_dir.parent
    initiative_texts = _initiative_texts(ws)
    items = [
        {**item, "repo": route.parse_frontmatter(initiative_texts.get(item["initiative"], ""))[0].get("repo")}
        for item in _work_items(ws)
    ]
    rows = runs_stranded.stranded(task_records, items)
    if a.json:
        print(json.dumps(rows))
        return 0
    if not rows:
        print("no stranded work")
        return 0
    print(records.format_table(rows, ["run", "task", "phase", "branch", "remedy"]))
    return 0


def _runs_trace(a: argparse.Namespace) -> int:
    d = Path(a.runs_dir) / f"{a.run_id}-trace"
    files = sorted(d.glob(f"{a.role}-*.jsonl" if a.role else "*.jsonl"), key=lambda p: (p.stem.rsplit("-", 1)[0], int(p.stem.rsplit("-", 1)[1])))
    rows = []
    for f in files:
        s = records.trace_summary(records.load_trace(f))
        rows.append({"node": f.stem, "turns": s["turns"] or 0, "cost_usd": float(s["cost_usd"] or 0), "result": s["subtype"] or "?",
                     "bash": s["tools"].get("Bash", 0), "reads": sum(s["reads"].values()), "whole": s["whole_file_reads"]})
        if a.verbose:
            print(f"== {f.stem}: tools={s['tools']} reads={s['reads']} whole_file_reads={s['whole_file_reads']}")
            for c in s["commands"][:12]:
                print("   $", c)
    print(records.format_table(rows, ["node", "turns", "cost_usd", "result", "bash", "reads", "whole"]))
    return 0


def _runs_clean(a: argparse.Namespace) -> int:
    repo = Path(a.repo).expanduser()
    runs_dir, reason = _runs_dir_for_land(a)
    if runs_dir is None:
        print(f"clean: {reason}")
        return 2
    records = {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in (runs_dir / a.run_id / "tasks").glob("*/*.json")}
    landed = {task for task, r in records.items() if r.get("landed")}
    dropped = {task for task, r in records.items() if r.get("status") == "dropped"}
    reasons = {task: r.get("reason", "unlanded") for task, r in records.items()}
    p = cleanup.plan_cleanup(run_id=a.run_id, worktrees=cleanup.git_worktrees(repo), branches=cleanup.git_branches(repo), worktree_root=a.worktree_root)
    for line in cleanup.apply_cleanup(repo, p, dry_run=not a.apply, landed=landed, dropped=dropped, reasons=reasons, force=a.force):
        print(line)
    if not a.apply:
        print("(dry run — pass --apply to do it)")
    return 0


def _runs_dir_for_land(a: argparse.Namespace) -> tuple[Path | None, str | None]:
    """`--runs-dir` overrides; otherwise `runs/` under the profile's `workspace_dir`."""
    if a.runs_dir:
        return Path(a.runs_dir).expanduser(), None
    profile_path = _profile_path(a)
    text = _read_text_or_none(profile_path)
    if text is None:
        return None, f"no profile at {profile_path}"
    try:
        profile = route.parse_profile(text)
    except route.ProfileError as exc:
        return None, f"profile unreadable: {exc}"
    workspace = profile.get("workspace_dir", "")
    if not workspace:
        return None, f"workspace_dir not set in profile {profile_path}"
    return Path(workspace).expanduser() / "runs", None


def _land_record(runs_dir: Path, run_id: str, task: str | None) -> tuple[dict | None, str, int]:
    tasks_root = runs_dir / run_id / "tasks"
    matches = sorted(tasks_root.glob(f"*/{task}.json" if task else "*/*.json"))
    if len(matches) != 1:
        return None, str(tasks_root.resolve()), len(matches)
    path = matches[0]
    record = json.loads(path.read_text(encoding="utf-8"))
    record.setdefault("run", run_id)
    record.setdefault("task", path.stem)
    record.setdefault("phase", path.parent.name)
    return record, str(path), 1


def _initiative_of(work_root: Path, task_id: str) -> str | None:
    """The one initiative whose `work/<initiative>/<phase>/*.md` holds an item
    with this id (`id` falls back to the file stem, as `route.work_item` does),
    or `None` when none or several do. The task record never names it."""
    found = set()
    for p in work_root.glob("*/*/*.md"):
        text = _read_text_or_none(p)
        if text is None:
            continue
        item = route.work_item(route.parse_frontmatter(text)[0], initiative=p.parent.parent.name, phase_dir=p.parent.name, stem=p.stem)
        if item["id"] == task_id:
            found.add(item["initiative"])
    return found.pop() if len(found) == 1 else None


def _initiative_of_phase(work_root: Path, phase: str) -> str | None:
    """The one initiative whose `work/<initiative>/<phase>/` holds a ticket, or
    `None` when none or several do. The phase record may not name it."""
    found = {p.parent.parent.name for p in work_root.glob(f"*/{phase}/*.md") if p.name != "initiative.md"}
    return found.pop() if len(found) == 1 else None


def _land_branches(repo: Path, record: dict, default_branch: str) -> dict[str, list[str]]:
    """Commit subjects ahead of `default_branch`, per candidate branch, with
    merge commits excluded by `git` (`--no-merges`) and patch-equivalent
    commits already on `default_branch` dropped (`--cherry-pick`)."""
    candidates = [f"agents/{record['run']}/{record['task']}", f"epic/{record.get('initiative')}/{record['phase']}"]
    branches: dict[str, list[str]] = {}
    for b in candidates:
        out = subprocess.run(["git", "-C", str(repo), "log", "--no-merges", "--cherry-pick", "--right-only", "--format=%s", f"{default_branch}...{b}"],
                             capture_output=True, text=True)
        if out.returncode == 0:
            branches[b] = [line for line in out.stdout.splitlines() if line]
    return branches


def _recover_branches(repo: Path, record: dict, phase_branch: str) -> dict[str, list[str]]:
    """Commit subjects ahead of the phase branch, per recover candidate
    branch — the same `git log --no-merges` shape `_land_branches` uses, but
    against the phase branch instead of the default branch, since recover
    merges INTO the phase branch rather than cherry-picking off it."""
    candidates = [f"agents/{record['run']}/{record['task']}", f"epic/{record.get('initiative')}/{record['phase']}--{record['task']}"]
    branches: dict[str, list[str]] = {}
    for b in candidates:
        out = subprocess.run(["git", "-C", str(repo), "log", "--no-merges", "--format=%s", f"{phase_branch}..{b}"], capture_output=True, text=True)
        if out.returncode == 0:
            branches[b] = [line for line in out.stdout.splitlines() if line]
    return branches


def _branch_exists(repo: Path, branch: str) -> bool:
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", branch], capture_output=True, text=True)
    return r.returncode == 0


def _git_out(repo: Path, *args: str) -> str | None:
    """Stripped stdout of `git -C repo <args>`, or `None` when git exits non-zero."""
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def _open_prs_for(repo: Path, branch: str) -> list[int] | str:
    """Numbers of the open PRs whose head is `branch`, or the reason they
    could not be listed."""
    try:
        r = subprocess.run(["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number"],
                           cwd=repo, capture_output=True, text=True)
    except OSError as exc:
        return f"could not list open pull requests for {branch}: {exc}"
    if r.returncode != 0:
        return f"could not list open pull requests for {branch}: {(r.stderr or r.stdout).strip()}"
    try:
        return [int(p["number"]) for p in json.loads(r.stdout or "[]")]
    except (ValueError, KeyError, TypeError):
        return f"could not read the open pull requests for {branch}: {r.stdout.strip()}"


def _land_resume(repo: Path, cherry_pick: dict) -> dict:
    """`land.resume_decision` for the `pr/<task>` branch a `cherry_pick` step
    would create. The expected tree is what cherry-picking the one commit onto
    `from` yields, computed by `git merge-tree` without touching the checkout.
    Open PRs are only asked for once every existing branch already matches."""
    branch, base = cherry_pick["onto"], cherry_pick["from"]
    local = _git_out(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{tree}}")
    fetched = subprocess.run(["git", "-C", str(repo), "fetch", "--quiet", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}"],
                             capture_output=True, text=True).returncode == 0
    remote = _git_out(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}^{{tree}}") if fetched else None
    if local is None and remote is None:
        return land.resume_decision("", None, None, [])
    shas = (_git_out(repo, "rev-list", f"{base}..{cherry_pick['branch']}") or "").split()
    merged = _git_out(repo, "merge-tree", "--write-tree", f"--merge-base={shas[0]}^", base, shas[0]) if len(shas) == 1 else None
    expected = merged.splitlines()[0] if merged else None
    if expected is None:
        return {"kind": "refuse", "reason": f"cannot compute the cherry-picked tree of {cherry_pick['branch']} onto {base}"}
    existing = [t for t in (local, remote) if t is not None]
    prs = _open_prs_for(repo, branch) if all(t == expected for t in existing) else []
    if isinstance(prs, str):
        return {"kind": "refuse", "reason": prs}
    decision = land.resume_decision(expected, local, remote, prs)
    if decision["kind"] != "refuse":
        return decision
    files = sorted({f for t in existing if t != expected
                    for f in (_git_out(repo, "diff-tree", "-r", "--name-only", expected, t) or "").split()})
    return {**decision, "reason": decision["reason"] + (f"; files differing: {', '.join(files)}" if files else "")}


def _land_enrich(steps: list[dict], *, path: str, worktree_root: str, task_paths: dict[str, str] | None = None,
                  item_path: str | None = None, workspace: str | None = None,
                  item_id: str | None = None) -> list[dict]:
    """Steps enriched with what only the edge knows: each `mark_done`'s own
    record file path (`task_paths` maps task name to path in phase mode), and
    the configured worktree root for `clean`/`clean_phase`. `item_path` (task
    mode only) is the work item `mark_done` will also try to close out. A
    `checks` step that names a `branch` (phase mode) gets no filesystem write
    here — a dry run must stay read-only, so the actual worktree is only ever
    created at execution time, inside `_execute_land_step`, under `--apply`.
    A `route_sync` step gets the `workspace` to sync and the item's own `id`
    (`item_id`, else the task name the plan used)."""
    def enrich(step: dict) -> dict:
        if step["kind"] == "route_sync":
            return {**step, "item": item_id or step["item"], "workspace": workspace}
        if step["kind"] == "mark_done":
            marked = {**step, "path": (task_paths or {}).get(step["task"], path)}
            return {**marked, "item": item_path, "from": "approved", "to": "done"} if item_path else marked
        if step["kind"] in ("clean", "clean_phase"):
            return {**step, "worktree_root": worktree_root}
        return step
    return [enrich(s) for s in steps]


def _land_phase_record(runs_dir: Path, run_id: str, phase: str) -> tuple[dict | None, list[dict], dict[str, str], str]:
    """The phase record at `runs/<run>:<phase>.json` and every task record
    filed under it, keyed by task name for `mark_done`'s own file path."""
    phase_path = runs_dir / f"{run_id}:{phase}.json"
    if not phase_path.exists():
        return None, [], {}, str(phase_path.resolve())
    phase_record = json.loads(phase_path.read_text(encoding="utf-8"))
    phase_record.setdefault("run", run_id)
    phase_record.setdefault("phase", phase)
    task_records, task_paths = [], {}
    for p in sorted((runs_dir / run_id / "tasks" / phase).glob("*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        r.setdefault("run", run_id); r.setdefault("task", p.stem); r.setdefault("phase", phase)
        task_records.append(r)
        task_paths[r["task"]] = str(p)
    return phase_record, task_records, task_paths, str(phase_path)


def _phase_items(work_root: Path, initiative: str, phase: str) -> tuple[list[dict] | None, str]:
    """The phase's own tickets from the work store (`work/<initiative>/<phase>/*.md`,
    `route.work_item`'s `state`), or `None` with the directory searched when
    there is nothing there to read — a phase can never be waved through by a
    missing ticket file."""
    phase_dir = work_root / initiative / phase
    paths = sorted(phase_dir.glob("*.md")) if phase_dir.is_dir() else []
    if not paths:
        return None, str(phase_dir)
    items = []
    for p in paths:
        text = _read_text_or_none(p)
        if text is None:
            continue
        fields = route.parse_frontmatter(text)[0]
        item = route.work_item(fields, initiative=initiative, phase_dir=phase, stem=p.stem)
        items.append({"id": item["id"], "status": item["state"]})
    return items, str(phase_dir)


def _phase_needing_land(runs_dir: Path, run_id: str) -> str | None:
    """The one phase under `runs/<run_id>/tasks/*/` holding more than one
    task record — phase mode's default trigger when neither `--phase` nor
    `--task` is given. `None` when no single phase qualifies, leaving task
    mode's own record count to speak (including its "found 0" refusal)."""
    tasks_root = runs_dir / run_id / "tasks"
    if not tasks_root.is_dir():
        return None
    candidates = [d.name for d in tasks_root.iterdir() if d.is_dir() and len(list(d.glob("*.json"))) > 1]
    return candidates[0] if len(candidates) == 1 else None


_LAUNCH_ERROR = "refuse checks: "  # a missing executable, marked so the edge refuses (exit 2) not just stops (exit 1)


def _run_checks(checks: list[tuple[str, list[str]]], cwd: Path) -> tuple[bool, str]:
    """Each `(name, argv)` pair in order, stopping at the first launch error
    or failure and naming it by `name`, never by its argv or shell command."""
    for name, argv in checks:
        try:
            r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
        except OSError as exc:
            return False, f"{_LAUNCH_ERROR}{name}: {exc}"
        if r.returncode != 0:
            return False, f"{name}: {(r.stderr or r.stdout).strip()}"
    return True, f"{len(checks)} checks passed"


def _execute_land_step(repo: Path, step: dict) -> tuple[bool, str]:
    kind = step["kind"]
    if kind == "pick_branch":
        return True, f"{step['branch']} ({step['commit_subject']})"
    if kind == "cherry_pick":
        co = subprocess.run(["git", "-C", str(repo), "checkout", "-b", step["onto"], step["from"]], capture_output=True, text=True)
        if co.returncode != 0:
            return False, co.stderr.strip() or co.stdout.strip()
        # The branch was chosen because it is exactly one commit ahead of
        # `from`, so that range names the commit without matching on the
        # subject text, which a second commit could share.
        rev = subprocess.run(["git", "-C", str(repo), "rev-list", f"{step['from']}..{step['branch']}"], capture_output=True, text=True)
        shas = [s for s in rev.stdout.split() if s]
        if rev.returncode != 0 or len(shas) != 1:
            return False, f"expected exactly one commit ahead of {step['from']} on {step['branch']}, found {len(shas)}"
        cp = subprocess.run(["git", "-C", str(repo), "cherry-pick", shas[0]], capture_output=True, text=True)
        if cp.returncode != 0:
            subprocess.run(["git", "-C", str(repo), "cherry-pick", "--abort"], capture_output=True, text=True)
            return False, cp.stderr.strip() or cp.stdout.strip()
        return True, f"cherry-picked {shas[0][:8]} onto {step['onto']}"
    if kind == "reuse_branch":
        args = ["checkout", step["branch"]] if step["local"] else ["checkout", "-b", step["branch"], f"origin/{step['branch']}"]
        co = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
        return co.returncode == 0, (f"reusing {step['branch']}" if co.returncode == 0 else co.stderr.strip() or co.stdout.strip())
    if kind == "checks":
        if "branch" in step:
            # Phase mode: never check out the phase branch in the working
            # repo (that races anything else using it) — build it fresh in
            # its own worktree instead, and remove it whatever happens. The
            # directory is allocated here, at execution, not in the plan
            # (`land.py`/`_land_enrich`), so a dry run never touches disk.
            wt = Path(tempfile.mkdtemp(prefix="land-checks-"))
            add = subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", str(wt), step["branch"]], capture_output=True, text=True)
            if add.returncode != 0:
                return False, add.stderr.strip() or add.stdout.strip()
            try:
                return _run_checks(step["checks"], wt)
            finally:
                subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)], capture_output=True)
        return _run_checks(step["checks"], repo)
    if kind == "push":
        r = subprocess.run(["git", "-C", str(repo), "push", "-u", "origin", step["branch"]], capture_output=True, text=True)
        return r.returncode == 0, (step["branch"] if r.returncode == 0 else r.stderr.strip() or r.stdout.strip())
    if kind == "pr_create":
        r = subprocess.run(["gh", "pr", "create", "--title", step["title"], "--body", step["body"]], cwd=repo, capture_output=True, text=True)
        return r.returncode == 0, (r.stdout.strip() or r.stderr.strip())
    if kind == "wait_checks":
        return _wait_checks(repo, float(step.get("timeout_s", 180)))
    if kind == "merge":
        r = subprocess.run(["gh", "pr", "merge", "--squash", "--delete-branch"], cwd=repo, capture_output=True, text=True)
        return r.returncode == 0, (r.stdout.strip() or r.stderr.strip())
    if kind == "clean":
        wt = Path(step["worktree_root"]).expanduser() / step["run"] / step["task"]
        if wt.exists():
            subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)], capture_output=True)
        subprocess.run(["git", "-C", str(repo), "branch", "-D", step["branch"]], capture_output=True)
        return True, f"deleted branch {step['branch']}"
    if kind == "clean_phase":
        doomed = [b for b in cleanup.git_branches(repo) if b == step["phase_branch"] or b.startswith(step["phase_branch"] + "--")]
        doomed += [f"agents/{step['run']}/{t}" for t in step["tasks"]]
        for t in step["tasks"]:
            wt = Path(step["worktree_root"]).expanduser() / step["run"] / t
            if wt.exists():
                subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)], capture_output=True)
        for b in doomed:
            subprocess.run(["git", "-C", str(repo), "branch", "-D", b], capture_output=True)
        return True, "deleted " + ", ".join(doomed)
    if kind == "mark_done":
        record_path = Path(step["path"])
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record_path.write_text(json.dumps({**record, "landed": True}, indent=2), encoding="utf-8")
        _close_approved_item(step.get("item"))
        return True, f"{step['task']} marked landed at {record_path}"
    if kind == "route_sync":
        # A failed sync is reported, never a failed land: after the merge nothing is undone,
        # before the PR it opens without `Closes`.
        when = "before the PR" if step.get("before") else "after the merge"
        tail = "the PR opens without Closes" if step.get("before") else "land not undone"
        sync = argparse.Namespace(item=step["item"], workspace=step["workspace"], dry_run=False, project=None, profile=None)
        try:
            rc = _route_sync(sync)
        except Exception as exc:
            return True, f"sync of {step['item']} failed {when} ({type(exc).__name__}: {exc}); {tail}"
        return True, f"synced {step['item']}" if rc == 0 else f"sync of {step['item']} failed {when} (exit {rc}); {tail}"
    return False, f"unknown step {kind!r}"


def _land_item_facts(item_path: str | None) -> tuple[str | None, str | None]:
    """`(id, issue)` from the work item's frontmatter, each `None` when the
    item is absent or does not carry it. `issue` is whatever the sync wrote
    back, normally a bare number."""
    text = _read_text_or_none(Path(item_path)) if item_path else None
    fields = route.parse_frontmatter(text)[0] if text is not None else {}
    item_id, issue = fields.get("id"), fields.get("issue")
    return (str(item_id) if item_id else None), (str(issue) if issue else None)


def _resolved_tracker(runs_dir: Path) -> str:
    """`tracker` from `<runs_dir>/policy.tracker.json` when a landing step has
    dropped one there; `github-projects` when absent, unreadable or not a name.
    A remote-is-github.com check is not made here."""
    text = _read_text_or_none(runs_dir / "policy.tracker.json")
    try:
        raw = json.loads(text) if text is not None else None
    except json.JSONDecodeError:
        raw = None
    tracker = raw.get("tracker") if isinstance(raw, dict) else None
    return tracker if isinstance(tracker, str) and tracker else "github-projects"


def _close_approved_item(item_path: str | None) -> None:
    """Moves the work item at `item_path` from `approved` to `done`, prints
    the reason and leaves it when it is any other state, does nothing when
    `item_path` is `None` or names no file on disk (no work item to close)
    or is already `done`."""
    if item_path is None:
        return
    item = Path(item_path)
    if not item.exists():
        return
    new_text, message = land.approve_to_done(item.read_text(encoding="utf-8"))
    if new_text is not None:
        item.write_text(new_text, encoding="utf-8")
    elif message:
        print(message)


def _await_checks(poll, timeout_s: float = 180.0, sleep=time.sleep, now=time.monotonic) -> tuple[bool, str]:
    """`poll() -> (returncode, output)` until green or failed. No check yet means not yet: retry every 15s for `timeout_s`."""
    started, waiting = now(), False
    while True:
        rc, output = poll()
        decision = land.wait_decision(rc, output, now() - started, timeout_s)
        if decision == "retry":
            if not waiting:
                print(f"{output.strip()}; polling every 15s until they finish" if land.is_pending(rc)
                      else f"no checks reported yet, waiting up to {timeout_s:.0f}s for the first one to appear")
            waiting = True
            sleep(15)
        elif decision == "timeout":
            return False, f"no checks reported within {timeout_s:.0f}s"
        else:
            return decision == "green", "green" if decision == "green" else output.strip()


def _read_checks(repo: Path):
    """`(True, (check_runs, status))` for HEAD's REST check bodies, or `(False, detail)` on a failed or unparseable call."""
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True)
    if head.returncode != 0:
        return False, head.stderr.strip() or "git rev-parse HEAD failed"
    argvs = land.rest_checks_argvs(head.stdout.strip())
    bodies = []
    for argv, key in zip(argvs, ("check_runs", "statuses")):
        r = subprocess.run(argv, cwd=repo, capture_output=True, text=True)
        body = land.merge_pages(r.stdout or "", key) if r.returncode == 0 else None
        if body is None:
            return False, (r.stderr or r.stdout or "").strip() or f"unreadable output from {argv[-1]}"
        bodies.append(body)
    return True, tuple(bodies)


def _wait_checks(repo: Path, timeout_s: float, sleep=time.sleep, now=time.monotonic) -> tuple[bool, str]:
    # Edge bend (A2): a count of consecutive unreadable polls, reset by any readable one.
    errors = 0

    def poll() -> tuple[int, str]:
        nonlocal errors
        ok, value = _read_checks(repo)
        errors = 0 if ok else errors + 1
        if ok:
            return land.check_poll_result(*value)
        result = land.unreadable_poll(errors, value)
        if land.is_pending(result[0]):
            sleep(land.poll_backoff_s(errors))  # on top of the 15s between polls: a rate limit needs room
        return result
    return _await_checks(poll, timeout_s, sleep, now)


def _repo_is_dirty(repo: Path) -> bool:
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True)
    return bool(status.stdout.strip())


def _runs_land(a: argparse.Namespace) -> int:
    repo = Path(a.repo).expanduser()
    runs_dir, reason = _runs_dir_for_land(a)
    if runs_dir is None:
        print(f"land: {reason}")
        return 2
    if a.apply:
        guard_rc = _leader_guard_or_refuse(runs_dir, _holder_label(a), a.force, claim=not a.no_claim)
        if guard_rc is not None:
            return guard_rc
    default_branch = "main"
    repo_facts = {"venv_python": (repo / ".venv" / "bin" / "python").exists(), "uv_lock": (repo / "uv.lock").exists()}
    phase = getattr(a, "phase", None) or (None if a.task else _phase_needing_land(runs_dir, a.run_id))
    record, item_path = None, None
    if phase:
        phase_record, task_records, task_paths, searched = _land_phase_record(runs_dir, a.run_id, phase)
        if phase_record is None:
            print(f"land: no phase record at {searched}")
            return 2
        initiative = phase_record.get("initiative") or _initiative_of_phase(runs_dir.parent / "work", phase)
        items, items_path = (_phase_items(runs_dir.parent / "work", initiative, phase)
                              if initiative else (None, f"{runs_dir.parent / 'work'} (no single initiative holds phase {phase})"))
        if items is None:
            print(f"land: no work items at {items_path}, expected the phase's tickets")
            return 2
        plan_steps = land.land_plan({**phase_record, "initiative": initiative}, {}, default_branch, repo_facts,
                                    items=items, task_records=task_records)
        steps = _land_enrich(plan_steps, path=searched, worktree_root=a.worktree_root, task_paths=task_paths)
    else:
        record, searched, count = _land_record(runs_dir, a.run_id, a.task)
        if record is None:
            print(f"land: looked in {searched}, found {count} task records, expected 1")
            return 2
        branches = _land_branches(repo, record, default_branch)
        item_path = (str(runs_dir.parent / "work" / record["initiative"] / record["phase"] / f"{record['task']}.md")
                     if record.get("initiative") else None)
        item_id, issue = _land_item_facts(item_path)
        plan_steps = land.land_plan(record, branches, default_branch, repo_facts,
                                    tracker=_resolved_tracker(runs_dir), issue=issue)
        steps = _land_enrich(plan_steps, path=searched, worktree_root=a.worktree_root, item_path=item_path,
                              workspace=str(runs_dir.parent), item_id=item_id)
    level = a.gate or _resolved_gate_level(runs_dir)
    if a.gate:
        print(f"land: --gate {level} overrides the resolved level")
    planned, steps = steps, land.gate_steps(steps, level)
    if not a.apply:
        print(json.dumps(steps, indent=2))
        return 2 if any(s["kind"] == "refuse" for s in steps) else 0
    if _repo_is_dirty(repo):
        print(f"land: refusing, {repo} is dirty")
        return 2
    cherry_pick = next((s for s in steps if s["kind"] == "cherry_pick"), None)
    if cherry_pick is not None:
        # An existing pr/<task> is not necessarily stale or foreign: a retried
        # push can leave a same-tree branch behind, and the rerun should reuse it.
        decision = _land_resume(repo, cherry_pick)
        if decision["kind"] == "refuse":
            print(f"land: refusing, branch {cherry_pick['onto']} already exists in {repo}: {decision['reason']}")
            return 2
        if decision["kind"] == "resume":
            print(f"land: resuming on existing branch {cherry_pick['onto']}")
        steps = land.resume_steps(steps, decision, cherry_pick["onto"])
        planned = land.resume_steps(planned, decision, cherry_pick["onto"])
    pr = ""
    for i in range(len(steps)):
        step = steps[i]
        if step["kind"] == "refuse":
            print(f"refused: {step['reason']}")
            return 2
        if step["kind"] == "note":
            continue
        ok, detail = _execute_land_step(repo, step)
        if step.get("before") == "pr_create":
            # The sync may have just written `issue:`; the PR opened next must carry its `Closes`.
            steps = land.with_issue(steps, record, _land_item_facts(item_path)[1])
        pr = detail if step["kind"] == "pr_create" and ok else pr
        if step["kind"] == "checks" and not ok and detail.startswith(_LAUNCH_ERROR):
            # A check whose executable `subprocess` can't find is a refusal,
            # not an ordinary failure, and it fires before `push` so a check
            # that never ran leaves no pushed branch behind.
            print(detail)
            return 2
        print(f"{step['kind']}: {detail}")
        if not ok:
            remaining = [s["kind"] for s in steps[i + 1:]]
            print("stopped; remaining: " + ", ".join(remaining))
            return 1
        if step["kind"] == "wait_checks" and a.no_merge:
            print("stopping after wait_checks (--no-merge)")
            return 0
    stop = land.gate_stop(planned, steps, level, pr)
    if stop:
        print(stop)
    return 3 if stop else 0


def _runs_recover(a: argparse.Namespace) -> int:
    """Merge one approved task's commit into its phase branch, the remedy for
    a merge the harness escalated to `self_modification` and so never
    applied. Never runs the repo's checks, never pushes: this only lands one
    commit on a local phase branch, and the gate to `main` stays a person's."""
    repo = Path(a.repo).expanduser()
    runs_dir, reason = _runs_dir_for_land(a)
    if runs_dir is None:
        print(f"recover: {reason}")
        return 2
    record, searched, count = _land_record(runs_dir, a.run_id, a.task_id)
    if record is None:
        print(f"recover: looked in {searched}, found {count} task records, expected 1")
        return 2
    work_root = runs_dir.parent / "work"
    initiative = _initiative_of(work_root, a.task_id)
    if initiative is None:
        print(f"recover: {a.task_id}: no single initiative under {work_root} holds this task")
        return 2
    record = land.recover_record(searched, record.get("ticket"), initiative)
    if record.get("kind") == "refuse":
        print(f"recover: {record['reason']}")
        return 2
    phase = record["phase"]
    phase_branch = f"epic/{initiative}/{phase}"
    if not _branch_exists(repo, phase_branch):
        print(f"recover: phase branch {phase_branch} does not exist in {repo}")
        return 2
    item_path = str(work_root / initiative / phase / f"{a.task_id}.md")
    mark_done_step = {"kind": "mark_done", "item": item_path, "from": "approved", "to": "done"}
    branches = _recover_branches(repo, record, phase_branch)
    step = land.recover_plan(record, branches)[0]
    if step["kind"] == "already_recovered":
        print(f"recover: {step['branch']} already contains the commit ({step['reason']}); nothing to do")
        if a.dry_run:
            print(json.dumps(mark_done_step))
            return 0
        _close_approved_item(item_path)
        return 0
    if step["kind"] == "refuse":
        print(f"refused: {step['reason']}")
        return 2
    source, target, subject = step["source"], step["target"], step["commit_subject"]
    message = f"Merge branch '{source}' into {target}"
    if a.dry_run:
        print(f"recover: would merge {source} into {target} --no-ff -m {message!r} ({subject!r})")
        print(json.dumps(mark_done_step))
        return 0
    before = subprocess.run(["git", "-C", str(repo), "rev-parse", target], capture_output=True, text=True).stdout.strip()
    wt = Path(tempfile.mkdtemp(prefix="recover-"))
    add = subprocess.run(["git", "-C", str(repo), "worktree", "add", str(wt), target], capture_output=True, text=True)
    if add.returncode != 0:
        print(f"refused: could not check out {target}: {add.stderr.strip() or add.stdout.strip()}")
        return 2
    try:
        merge = subprocess.run(["git", "-C", str(wt), "merge", "--no-ff", source, "-m", message], capture_output=True, text=True)
        if merge.returncode != 0:
            subprocess.run(["git", "-C", str(wt), "merge", "--abort"], capture_output=True, text=True)
            print(f"refused: merge of {source} into {target} was not clean: {merge.stderr.strip() or merge.stdout.strip()}")
            return 2
    finally:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)], capture_output=True)
    after = subprocess.run(["git", "-C", str(repo), "rev-parse", target], capture_output=True, text=True).stdout.strip()
    diff = subprocess.run(["git", "-C", str(repo), "diff", "--stat", before, after], capture_output=True, text=True)
    print(f"recover: {target} {before[:8]} -> {after[:8]}")
    print(diff.stdout.strip())
    _close_approved_item(item_path)
    return 0


def _epic_watch(a: argparse.Namespace) -> int:
    out = epic.watch(a.pidfile, log=a.log, max_seconds=a.max_seconds, interval=a.interval)
    print(json.dumps(out, indent=2) if a.json else "\n".join(
        [f"pid {out['pid']}: {'finished' if out['finished'] else 'still running'}",
         *out.get("quarantined", []), *out.get("reused", []), *(x for x in (out.get("summary"), out.get("usage")) if x)]))
    return 0 if out["finished"] else 3






def _plan_serve(a: argparse.Namespace) -> int:
    if a.check:
        print(plan.check(a.dir))
    print(plan.serve(a.dir, kind=a.kind, open_browser=not a.no_open)); return 0


DEFAULT_PROFILE = "~/.config/agent-tools/profile.yaml"


def _profile_path(a: argparse.Namespace) -> Path:
    return Path(a.profile or os.environ.get("AGENT_TOOLS_PROFILE") or DEFAULT_PROFILE).expanduser()


def _read_text_or_none(p: Path):
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def _mtime_iso(p: Path):
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    return datetime.datetime.fromtimestamp(mtime, tz=datetime.UTC).isoformat()


def _gather_context(profile_path: Path):
    """Read the profile and the workspace; return (profile_or_none, reason, intake, runs, initiatives, problems)."""
    text = _read_text_or_none(profile_path)
    if text is None:
        return None, f"no profile at {profile_path}", [], [], [], []
    try:
        profile = route.parse_profile(text)
    except route.ProfileError as exc:
        return None, f"profile unreadable: {exc}", [], [], [], []
    workspace = profile.get("workspace_dir", "")
    if not workspace:
        return profile, "workspace_dir not set in profile", [], [], [], []
    ws = Path(workspace).expanduser()
    pid_paths = sorted((ws / "runs").glob("*.pid"))
    pids = {p.stem: t for p in pid_paths if (t := _read_text_or_none(p)) is not None}
    alive = {rid: epic.run_live(route.parse_pid(t), ws / "runs" / f"{rid}.pid") for rid, t in pids.items()}
    started = {rid: _mtime_iso(ws / "runs" / f"{rid}.pid") for rid in pids}
    # the initiative id is the DIRECTORY name: work/<initiative>/<phase>/<task>.md;
    # the edge only reads and names the path parts — route.work_item normalises
    items = _work_items(ws)
    return (profile, "",
            _intake_groups(ws, items),
            route.run_entries(pids, alive, started),
            route.initiative_summaries(items),
            route.state_problems(items))


def _route_context(a: argparse.Namespace) -> int:
    # The edge is allowed to catch everything _gather_context can raise: this
    # command must never take a session down with it (spec §2). That
    # tolerance is for profile/workspace file reads only — it stops at the
    # gatherer, per charter A6, so a bug in the usage assessment surfaces
    # instead of erasing an otherwise-good docket (run tools-pacing-7).
    try:
        profile, reason, intake, runs, initiatives, problems = _gather_context(_profile_path(a))
    except Exception as exc:
        print(f"routing: context unavailable ({type(exc).__name__}: {exc})")
        return 0
    # Computed once via the gatherer, same reason `route launch` gates on;
    # only when there is a workspace to gather usage files from.
    usage_reason = (
        _usage_assessment(
            Path(profile["workspace_dir"]).expanduser() / "runs",
            profile.get("window_ceiling_usd"), profile.get("weekly_ceiling_usd"),
        ).reason
        if not reason and profile is not None else None
    )
    if a.json:
        doc = route.context_document(profile, intake, runs, initiatives, problems)
        if reason:
            doc["reason"] = reason
        if usage_reason is not None:
            doc["usage"] = usage_reason
        print(json.dumps(doc, indent=2))
    elif reason:
        # nothing was read, so print no counts: an unread workspace must not
        # look like an empty one
        empty_groups = {"queued": [], "decomposed": [], "landed": []}
        first_line = route.render_context(profile, empty_groups, [], []).partition("\n")[0]
        print(f"{first_line} ({reason})")
    else:
        gate_level = _resolved_gate_level(Path(profile["workspace_dir"]).expanduser() / "runs")
        print(f"{route.render_context(profile, intake, runs, initiatives, problems, gate_level=gate_level)}\nusage: {usage_reason}")
    return 0


def _refuse_if_already_running(runs_dir: Path, prefix: str):
    """spec: at most one live run per `prefix` — the single-writer rule
    applied to a run-id prefix. Returns the refusal line to print, or
    None when no pidfile under `prefix` names a live pid."""
    live_pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)\.pid$")
    for pidfile in sorted(runs_dir.glob(f"{prefix}-*.pid")):
        if not live_pattern.fullmatch(pidfile.name):
            continue
        pid_text = _read_text_or_none(pidfile)
        pid = route.parse_pid(pid_text) if pid_text is not None else None
        if epic.run_live(pid, pidfile):
            return f"routing: {pidfile.stem} is already running (pid {pid})"
    return None


def _work_items(ws: Path) -> list:
    return [
        route.work_item(fields, initiative=task_path.parent.parent.name, phase_dir=task_path.parent.name, stem=task_path.stem)
        for task_path in sorted((ws / "work").glob("*/*/*.md"))
        if task_path.name != "initiative.md"
        for text in [_read_text_or_none(task_path)] if text is not None
        for fields in [route.parse_frontmatter(text)[0]]
    ]


def _initiative_texts(ws: Path) -> dict:
    return {
        p.name: _read_text_or_none(p / "initiative.md") or ""
        for p in sorted((ws / "work").glob("*"))
        if (p / "initiative.md").is_file()
    }


def _intake_groups(ws: Path, items: list) -> dict:
    intake_root = ws / "intake"
    paths = sorted(intake_root.glob("*.md")) + sorted((intake_root / "done").glob("*.md"))
    files = {str(p.relative_to(intake_root)): t for p in paths if (t := _read_text_or_none(p)) is not None}
    texts = _initiative_texts(ws)
    states = route.initiative_states(sorted(texts), items)
    initiatives = [{"id": iid, "done": states[iid], "text": texts[iid]} for iid in texts]
    return route.intake_groups(route.intake_entries(files), initiatives)


def _intake_groups_for(ws: Path):
    """`_intake_groups` from disk, or `None` with no `intake/` dir."""
    return _intake_groups(ws, _work_items(ws)) if (ws / "intake").is_dir() else None


def _status_rows_for(runs_dir: Path) -> list:
    pids = {p.stem: t for p in sorted(runs_dir.glob("*.pid")) if (t := _read_text_or_none(p)) is not None}
    alive = {run_id: epic.run_live(route.parse_pid(t), runs_dir / f"{run_id}.pid") for run_id, t in pids.items()}
    started = {run_id: _mtime_iso(runs_dir / f"{run_id}.pid") for run_id in pids}
    runs = route.run_entries(pids, alive, started)
    summaries = {p.stem: epic.summarize_log(_read_text_or_none(p) or "") for p in runs_dir.glob("*.log")}
    return route.status_rows(route.status_entries(runs, summaries))


def _route_status(a: argparse.Namespace) -> int:
    profile_path = _profile_path(a)
    text = _read_text_or_none(profile_path)
    if text is None:
        print(f"routing: no profile at {profile_path}")
        return 2
    try:
        profile = route.parse_profile(text)
    except route.ProfileError as exc:
        print(f"routing: profile unreadable: {exc}")
        return 2
    workspace = profile.get("workspace_dir", "")
    if not workspace:
        print(f"routing: workspace_dir not set in profile {profile_path}")
        return 2
    # Past this point a stray pidfile — hand-edited, absurdly large — must
    # not take the session down with it (spec §2); charter A6 puts
    # exceptions at the edge, matching _route_context's guard above.
    try:
        ws = Path(workspace).expanduser()
        rows = _status_rows_for(ws / "runs")
        groups = _intake_groups_for(ws)
        problems = route.state_problems(_work_items(ws))
        if a.json:
            # The bare rows list is the shape older callers read; it stays when there
            # is nothing else to say. A problem is never dropped for lack of an intake dir.
            if groups is None and not problems:
                doc = rows
            elif groups is None:
                doc = {"runs": rows, "problems": problems}
            else:
                doc = {"runs": rows, "intake": groups, "problems": problems}
            print(json.dumps(doc, indent=2))
        else:
            print(route.render_status(rows, groups, problems, gate_level=_resolved_gate_level(ws / "runs")))
    except Exception as exc:
        print(f"routing: status unavailable ({type(exc).__name__}: {exc})")
    return 0


def _leader_runs_dir_or_refuse(a: argparse.Namespace):
    """spec: the leader lock lives under the profile's workspace, same as
    `route status`'s runs dir. Returns (profile, runs_dir, None) or
    (None, None, 2) after printing the refusal."""
    profile_path = _profile_path(a)
    text = _read_text_or_none(profile_path)
    if text is None:
        print(f"routing: no profile at {profile_path}")
        return None, None, 2
    try:
        profile = route.parse_profile(text)
    except route.ProfileError as exc:
        print(f"routing: profile unreadable: {exc}")
        return None, None, 2
    workspace = profile.get("workspace_dir", "")
    if not workspace:
        print(f"routing: workspace_dir not set in profile {profile_path}")
        return None, None, 2
    return profile, Path(workspace).expanduser() / "runs", None


def _leader_heartbeat_minutes() -> int:
    """`policy.leader.heartbeat_minutes` from the resolved cartridge dict, default 10.
    Resolving a cartridge's own policy is another repository's item, and
    `route.parse_profile`'s flat `key: scalar` schema (spec §1) has no `policy` key to
    read in the meantime, so this always answers the default until that lands."""
    return chair.DEFAULT_HEARTBEAT_MINUTES


def _leader_read_or_refuse(runs_dir: Path):
    """A file present but unreadable or not valid JSON is a refusal, not a free lock
    (charter A6: `chair.read` raises at the edge; here is where that becomes a value).
    Returns `(record_or_none, None)` or `(None, 2)` after printing the reason."""
    try:
        return chair.read(runs_dir), None
    except (OSError, json.JSONDecodeError) as exc:
        print(f"chair: lock file unreadable ({type(exc).__name__}: {exc})")
        return None, 2


def _holder_label(a: argparse.Namespace) -> str:
    return getattr(a, "label", None) or os.environ.get("COX_SESSION_LABEL") or "unlabeled"


def _leader_guard_or_refuse(runs_dir: Path, holder: str, force: bool, claim: bool = False) -> int | None:
    """Exit code 2 with the refusal printed when another live session holds the loop;
    None to proceed. `claim` takes an unheld, stale or crashed lock for `holder` before
    proceeding, so launching or landing is what makes a session the leader."""
    record, rc = _leader_read_or_refuse(runs_dir)
    if rc is not None:
        return None
    state = chair.liveness(record, _leader_pid_alive(record), datetime.datetime.now(datetime.UTC), socket.gethostname(), _leader_heartbeat_minutes())
    line = chair.guard(record, holder, state)
    if line is not None:
        print(f"override: {line}" if force else line)
        return None if force else 2
    if claim and state != "live":
        pid, host = _leader_identity()
        new_record, _ = chair.take(record, holder, pid, host, datetime.datetime.now(datetime.UTC), _leader_heartbeat_minutes(), _leader_pid_alive(record), steal=True)
        chair.write(runs_dir, new_record)
        print(f"taking the loop: {holder}" if record is None else f"taking the loop from {record.get('session')} ({state})")
    return None


def _leader_identity(explicit_pid: int | None = None) -> tuple[int, str]:
    """The pid and host that name *this session* to the lock, for `take`/`beat`/`release`.
    `os.getpid()` names this one `cox` invocation, which exits the moment the command
    returns. `os.getppid()` names the process that invoked `cox`, which is the right
    answer only when a single long-running shell issues `take`, `beat` and `release` in
    turn; a caller that runs each command in a fresh shell records a pid that is dead
    before the next command, so its lock reads as stale the instant it is taken.
    `--pid` lets such a caller name the durable process that actually owns the loop."""
    return explicit_pid or os.getppid(), socket.gethostname()


def _leader_pid_alive(record: dict | None) -> bool:
    """A pid is judged dead only on the host that recorded it: this process has no way
    to check a pid number on another machine, so a foreign host's lock is never read as
    dead here — only its own host, or its heartbeat aging out, can make it stale."""
    if record is None:
        return False
    pid = record.get("pid")
    if not isinstance(pid, int):
        return False
    if record.get("host") != socket.gethostname():
        return True
    return chair.pid_alive(pid)


def _leader_refuse_dead_pid(explicit_pid: int | None) -> int | None:
    """Refuses only an explicit `--pid`; the `os.getppid()` default is alive by construction."""
    if explicit_pid is None or chair.pid_alive(explicit_pid):
        return None
    print(f"chair pid not alive: {explicit_pid}")
    return 2


def _leader_launched_by(record: dict | None, pid_alive_: bool, now: datetime.datetime, host: str, heartbeat_minutes: int) -> str | None:
    """The label to stamp on a run launched right now: the lock's own holder when it
    reads live, `None` when it reads stale, crashed, or none — never a guess."""
    if chair.liveness(record, pid_alive_, now, host, heartbeat_minutes) != "live":
        return None
    return record.get("session")


def _print_if_stale(record: dict | None, state: str) -> None:
    if record is not None and state in ("stale", "crashed"):
        print(f"chair {state}: {record.get('session', '?')} (pid {record.get('pid', '?')}) on {record.get('host', '?')}")


def _route_chair_take(a: argparse.Namespace) -> int:
    _profile, runs_dir, refuse_rc = _leader_runs_dir_or_refuse(a)
    if refuse_rc is not None:
        return refuse_rc
    session = a.label or "chair"
    pid, host = _leader_identity(a.pid)
    refuse_rc = _leader_refuse_dead_pid(a.pid)
    if refuse_rc is not None:
        return refuse_rc
    heartbeat_minutes = _leader_heartbeat_minutes()
    with chair.locked(runs_dir):
        record, read_rc = _leader_read_or_refuse(runs_dir)
        if read_rc is not None:
            return read_rc
        now = datetime.datetime.now(datetime.UTC)
        alive = _leader_pid_alive(record)
        prior_state = chair.liveness(record, alive, now, host, heartbeat_minutes)
        _print_if_stale(record, prior_state)
        new_record, reason = chair.take(record, session, pid, host, now, heartbeat_minutes, alive, steal=a.steal)
        if new_record is None:
            print(reason)
            return 2
        chair.write(runs_dir, new_record)
    print(f"chair taken: {new_record['session']} (pid {new_record['pid']}) on {new_record['host']}")
    return 0


def _route_chair_beat(a: argparse.Namespace) -> int:
    _profile, runs_dir, refuse_rc = _leader_runs_dir_or_refuse(a)
    if refuse_rc is not None:
        return refuse_rc
    session = a.label or "chair"
    pid, host = _leader_identity(a.pid)
    refuse_rc = _leader_refuse_dead_pid(a.pid)
    if refuse_rc is not None:
        return refuse_rc
    with chair.locked(runs_dir):
        record, read_rc = _leader_read_or_refuse(runs_dir)
        if read_rc is not None:
            return read_rc
        new_record, reason = chair.beat(record, session, pid, host, datetime.datetime.now(datetime.UTC), run_id=a.run)
        if new_record is None:
            print(reason)
            return 2
        chair.write(runs_dir, new_record)
    print(f"chair heartbeat: {new_record['session']}")
    return 0


def _route_chair_release(a: argparse.Namespace) -> int:
    _profile, runs_dir, refuse_rc = _leader_runs_dir_or_refuse(a)
    if refuse_rc is not None:
        return refuse_rc
    session = a.label or "chair"
    pid, host = _leader_identity(a.pid)
    refuse_rc = _leader_refuse_dead_pid(a.pid)
    if refuse_rc is not None:
        return refuse_rc
    with chair.locked(runs_dir):
        record, read_rc = _leader_read_or_refuse(runs_dir)
        if read_rc is not None:
            return read_rc
        _new_record, reason = chair.release(record, session, pid, host)
        if reason:
            print(reason)
            return 2
        chair.write(runs_dir, None)
    print(f"chair released: {record['session']}")
    return 0


def _route_chair_status(a: argparse.Namespace) -> int:
    _profile, runs_dir, refuse_rc = _leader_runs_dir_or_refuse(a)
    if refuse_rc is not None:
        return refuse_rc
    record, read_rc = _leader_read_or_refuse(runs_dir)
    if read_rc is not None:
        return read_rc
    now = datetime.datetime.now(datetime.UTC)
    alive = _leader_pid_alive(record)
    state = chair.liveness(record, alive, now, socket.gethostname(), _leader_heartbeat_minutes())
    if not a.json:
        _print_if_stale(record, state)
    if a.json:
        print(json.dumps({**(record or {}), "state": state}, indent=2))
    elif record is None:
        print("chair: none")
    else:
        print(f"chair: {record['session']} (pid {record['pid']}) on {record['host']} — {state}")
    return 0


def _route_chair_clear(a: argparse.Namespace) -> int:
    """Holds `chair.locked` across the whole read-decide-unlink sequence, same as
    take/beat/release, so a `take` landing mid-clear cannot be discarded silently."""
    _profile, runs_dir, refuse_rc = _leader_runs_dir_or_refuse(a)
    if refuse_rc is not None:
        return refuse_rc
    with chair.locked(runs_dir):
        result = chair.clear(runs_dir, force=a.force)
    if result is None:
        print("chair: nothing cleared")
        return 0
    path, record = result
    print(f"chair cleared: {record.get('session', '?')} (pid {record.get('pid', '?')}) on {record.get('host', '?')} [{path.name}]")
    return 0


def _route_chair_chat(a: argparse.Namespace) -> int:
    """`--read` prints the thread (or, with `--since`, only what's unread); otherwise
    appends one line, as the operator by default or, with `--as-leader`, only when this
    process's pid and host match the live lock's holder — a reply must never be shown
    from a session that is not the leader."""
    _profile, runs_dir, refuse_rc = _leader_runs_dir_or_refuse(a)
    if refuse_rc is not None:
        return refuse_rc
    path = leader_chat.chat_path(runs_dir)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if a.read:
        thread = leader_chat.read_thread(existing, limit=50)
        entries = leader_chat.unread(thread, a.since)
        if a.json:
            print(json.dumps(entries))
        else:
            for entry in entries:
                print(f"{entry.get('at', '?')} {entry.get('from', '?')}: {entry.get('text', '')}")
        return 0
    if a.text is None:
        print("chat: TEXT required unless --read")
        return 2
    sender = "operator"
    if a.as_leader:
        record, read_rc = _leader_read_or_refuse(runs_dir)
        if read_rc is not None:
            return read_rc
        pid, host = _leader_identity()
        now = datetime.datetime.now(datetime.UTC)
        state = chair.liveness(record, _leader_pid_alive(record), now, host, _leader_heartbeat_minutes())
        if state != "live":
            print("chat: refusing --as-leader (no live lock held)")
            return 2
        if record.get("pid") != pid or record.get("host") != host:
            print(f"chat: refusing --as-leader (held by {record.get('session', '?')} (pid {record.get('pid', '?')}) on {record.get('host', '?')})")
            return 2
        sender = record.get("session", "leader")
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"at": datetime.datetime.now(datetime.UTC).isoformat(), "from": sender, "text": a.text}
    path.write_text(leader_chat.append_line(existing, entry), encoding="utf-8")
    print(f"chat: {sender}: {a.text}")
    return 0


_PROFILE_KEYS_FOR_LAUNCH = ("workspace_dir", "harness_dir", "team", "cartridges_dir", "provider_profile")


def _resolve_profile_or_refuse(a: argparse.Namespace):
    """Resolve the profile for `file`/`launch`, spec §7: unlike `context`,
    a missing profile or a profile missing a key `route.harness_argv` needs
    is a hard refusal here (exit 2), since both subcommands need real paths
    to write to or launch against, not a one-liner to print and carry on.

    Returns `(profile, None)` on success, `(None, 2)` after printing the
    reason.
    """
    profile_path = _profile_path(a)
    text = _read_text_or_none(profile_path)
    if text is None:
        print(f"routing: no profile at {profile_path}")
        return None, 2
    try:
        profile = route.parse_profile(text)
    except route.ProfileError as exc:
        print(f"routing: profile unreadable: {exc}")
        return None, 2
    for key in _PROFILE_KEYS_FOR_LAUNCH:
        if not profile.get(key):
            print(f"routing: {key} not set in profile {profile_path}")
            return None, 2
    return profile, None


def _write_mapping(mapping: dict, ws: Path):
    """Write `mapping` (relative path -> text) under `ws`; `None` on success, else the refusal to print."""
    targets = {rel: ws / rel for rel in mapping}
    existing = [str(path) for path in targets.values() if path.exists()]
    if existing:
        return f"routing: refusing to overwrite existing path(s): {', '.join(existing)}"
    for rel, path in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(mapping[rel], encoding="utf-8")
        print(str(path))
    return None


def _sync_filed_items(mapping: dict, ws: Path, a: argparse.Namespace) -> None:
    """Run the per-item sync for each intake or work item `mapping` wrote, so its issue exists from birth.
    A failed sync (gh down, rate-limited) never fails the file: one line names the item and the way back."""
    rels = [rel for rel in mapping
            if (len(Path(rel).parts) == 2 and Path(rel).parts[0] == "intake")
            or (len(Path(rel).parts) == 4 and Path(rel).parts[0] == "work")]
    for rel in rels:
        item_id = route.parse_frontmatter(mapping[rel])[0].get("id") or Path(rel).stem
        sync = argparse.Namespace(item=item_id, workspace=str(ws), dry_run=False, project=None, profile=a.profile)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                rc = _route_sync(sync)
        except Exception as exc:
            rc, out = 1, io.StringIO(f"{type(exc).__name__}: {exc}")
        lines = out.getvalue().strip().splitlines()
        reason = f"; {lines[-1]}" if rc != 0 and lines else ""
        print(f"synced {item_id}" if rc == 0
              else f"route file: wrote {item_id}; issue sync failed (exit {rc}{reason}); run `route sync --item {item_id}`")


def _route_file_from_intake(a: argparse.Namespace, ws: Path, profile: dict) -> int:
    intake_path = Path(a.from_intake)
    if intake_path.parent.resolve() != (ws / "intake").resolve():
        print(f"routing: --from-intake must name a file directly under {ws / 'intake'}")
        return 2
    intake_text = _read_text_or_none(intake_path)
    if intake_text is None:
        print(f"routing: cannot read intake file {a.from_intake}")
        return 2
    fields, intake_body = route.parse_frontmatter(intake_text)
    [entry] = route.intake_entries({intake_path.name: intake_text})
    repo = fields.get("repo", "")
    candidates = route.surface_candidates(intake_body, repo)
    surfaces = [c for c in candidates if (Path(repo) / c).is_file()]
    print(f"surfaces: {', '.join(surfaces)}" if surfaces else "surfaces: none resolved from the intake body")
    budget_usd = profile.get("one_task_budget_usd", 2.0)
    try:
        mapping = route.initiative_files(
            entry["title"], intake_body, repo, phase=a.phase,
            surfaces=surfaces, budget_usd=budget_usd, slug=fields.get("slug"),
        )
    except ValueError as exc:
        print(f"routing: {exc}")
        return 2
    slug = route.slugify(fields.get("slug") or entry["title"])
    initiative_rel = f"work/{slug}/initiative.md"
    new_initiative_text, new_intake_text = route.link_intake(
        mapping[initiative_rel], intake_text, f"intake/{intake_path.name}", slug
    )
    mapping[initiative_rel] = new_initiative_text
    mapping[f"intake/done/{intake_path.name}"] = new_intake_text
    refusal = _write_mapping(mapping, ws)
    if refusal:
        print(refusal)
        return 2
    intake_path.unlink()
    print(f"removed: {intake_path}")
    _sync_filed_items(mapping, ws, a)
    return 0


def _route_file(a: argparse.Namespace) -> int:
    profile, rc = _resolve_profile_or_refuse(a)
    if rc is not None:
        return rc
    ws = Path(profile["workspace_dir"]).expanduser()
    if a.from_intake:
        return _route_file_from_intake(a, ws, profile)
    if not a.title or not a.repo:
        print("routing: --title and --repo are required unless --from-intake is given")
        return 2
    if a.body == "-":
        body = sys.stdin.read()
    elif a.body:
        body = _read_text_or_none(Path(a.body))
        if body is None:
            print(f"routing: cannot read body file {a.body}")
            return 2
    else:
        body = ""
    try:
        if a.intake:
            date = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
            mapping = route.intake_file(a.title, body, a.repo, date)
        else:
            mapping = route.initiative_files(a.title, body, a.repo, phase=a.phase)
    except ValueError as exc:
        print(f"routing: {exc}")
        return 2
    refusal = _write_mapping(mapping, ws)
    if refusal:
        print(refusal)
        return 2
    _sync_filed_items(mapping, ws, a)
    return 0


def _harness_ready_or_refuse(harness_dir: str):
    """spec §4/§7: `launch` refuses before anything starts when the
    harness venv is not where the profile says. Returns 2 after printing
    the reason, or `None` when the venv is ready.
    """
    shell_py = Path(harness_dir) / "shell.py"
    venv_python = Path(harness_dir) / ".venv" / "bin" / "python"
    if not shell_py.exists() or not venv_python.exists():
        print(f"routing: harness venv missing at {harness_dir}; install it (see the harness README)")
        return 2
    return None


def _route_sync_state_path(profile_path: Path) -> Path:
    """Where the resolved `owner/N` project persists across runs: a sidecar
    next to the profile, not a key inside it. `route.parse_profile` accepts
    a fixed field set (`route.py`'s `_KNOWN_KEYS`), and `project` is not one
    of them, so writing it into the profile itself would leave that file
    unparsable by every other route command from then on."""
    return profile_path.with_name(profile_path.name + ".route-sync-project")


def _resolve_sync_project(a: argparse.Namespace, items: list, state_path: Path):
    """`(project, None)` on success, `(None, 2)` after printing the reason.
    `project` comes back `None` (no error) only for a dry-run preview run
    before any project exists — nothing to create yet, nothing to preview
    against either."""
    if a.project:
        return a.project, None
    cached = _read_text_or_none(state_path)
    if cached and cached.strip():
        return cached.strip(), None
    owner = items[0].repo.split("/", 1)[0] if items and "/" in items[0].repo else ""
    if not owner:
        print("route sync: no --project given and no repo to derive a project owner from")
        return None, 2
    ok, found = route_sync_gh.find_project(subprocess.run, owner)
    if not ok:
        print(f"route sync: {found}")
        return None, 2
    if found is not None:
        return f"{owner}/{found}", None
    if a.dry_run:
        return None, None
    ok, number = route_sync_gh.create_project(subprocess.run, owner)
    if not ok:
        print(f"route sync: {number}")
        return None, 2
    project = f"{owner}/{number}"
    state_path.write_text(f"{project}\n")
    return project, None


def _route_sync(a: argparse.Namespace) -> int:
    """Mirror the work store onto GitHub Projects: `--dry-run` renders the
    plan and touches nothing that outlives the run; otherwise `execute`
    runs it and stops at the first failed `gh` call. Refuses at once, exit
    2, when `gh` is not authenticated."""
    if not route_sync_gh.auth_ok(subprocess.run):
        print("route sync: gh is not authenticated; run `gh auth login`")
        return 2
    profile_path = _profile_path(a)
    text = _read_text_or_none(profile_path)
    try:
        profile = route.parse_profile(text) if text is not None else {}
    except route.ProfileError as exc:
        print(f"route sync: profile unreadable: {exc}")
        return 2
    workspace = a.workspace or profile.get("workspace_dir") or "."
    items = route_sync_gh.items_from_store(workspace)
    if a.item:
        items = [item for item in items if item.id == a.item]
        # Never fall through to the full listing: an unknown or shared id would run `item-list`.
        if len(items) != 1:
            print(f"route sync: --item {a.item!r} matches {len(items)} work-store items, expected exactly one")
            return 2
    project, rc = _resolve_sync_project(a, items, _route_sync_state_path(profile_path))
    if rc is not None:
        return rc
    repo_names = sorted({item.repo for item in items if item.repo})
    if a.item:
        ok, result = route_sync_gh.existing_item(subprocess.run, items[0].repo, items[0].issue, project)
    else:
        ok, result = route_sync_gh.existing(subprocess.run, repo_names, project)
    if not ok:
        print(f"route sync: {result}")
        return 2
    issues, project_items, item_node_ids = result
    steps = route_sync.plan(items, issues, project_items, "github-projects")
    if a.dry_run:
        print("\n".join(route_sync.render(steps)))
        return 0
    log = route_sync_gh.execute(steps, subprocess.run, project, workspace, items, item_node_ids)
    for kind, _, detail in log:
        print(f"{kind}: {detail}")
    hard_failure = any(not step_ok for kind, step_ok, _ in log if kind != "refuse")
    return 1 if hard_failure else 0


def _route_launch(a: argparse.Namespace) -> int:
    profile, rc = _resolve_profile_or_refuse(a)
    if rc is not None:
        return rc
    harness_dir = profile.get("harness_dir", "")
    venv_rc = _harness_ready_or_refuse(harness_dir)
    if venv_rc is not None:
        return venv_rc
    runs_dir = Path(profile["workspace_dir"]).expanduser() / "runs"
    if a.graph == "epic":
        # Only --include-blocked lifts this guard; --force never does.
        initiative_id = Path(a.initiative).name
        held = route.launch_blockers([
            item for item in _work_items(runs_dir.parent) if item["initiative"] == initiative_id
        ])
        if held and not a.include_blocked:
            print(f"routing: {initiative_id} has a ready task behind blocked {', '.join(held)}; pass --include-blocked to launch anyway")
            return 2
        if held:
            print(f"override: launching {initiative_id} despite blocked {', '.join(held)} (--include-blocked)")
    guard_rc = _leader_guard_or_refuse(runs_dir, _holder_label(a), a.force, claim=not a.no_claim)
    if guard_rc is not None:
        return guard_rc
    usage_code, usage_lines = route.launch_gate(
        _usage_assessment(runs_dir, profile.get("window_ceiling_usd"), profile.get("weekly_ceiling_usd")), a.force
    )
    for line in usage_lines:
        print(line)
    if usage_code is not None:
        return usage_code
    overlaid_provider_profile = None
    if a.tier_ceiling is not None or a.effort_ceiling is not None:
        provider_path = Path(profile["provider_profile"]).expanduser()
        provider_text = _read_text_or_none(provider_path)
        try:
            parsed = yaml.safe_load(provider_text) if provider_text is not None else None
        except yaml.YAMLError as exc:
            print(f"routing: provider profile at {provider_path} is not valid YAML: {exc}")
            return 2
        if not isinstance(parsed, dict):
            print(f"routing: no provider profile to apply a ceiling to at {provider_path}")
            return 2
        overlaid_provider_profile = route.overlay(parsed, a.tier_ceiling, a.effort_ceiling)
        if isinstance(overlaid_provider_profile, str):
            print(overlaid_provider_profile)
            return 2
    runs_dir.mkdir(parents=True, exist_ok=True)

    if a.graph == "epic":
        initiative_dir = Path(a.initiative)
        initiative_md = initiative_dir / "initiative.md"
        text = _read_text_or_none(initiative_md)
        if text is None:
            print(f"routing: no initiative.md at {initiative_md}")
            return 2
        fields, _ = route.parse_frontmatter(text)
        repo = a.repo or fields.get("repo")
        if not repo:
            print(f"routing: no --repo given and no repo: in {initiative_md}")
            return 2
        try:
            status = subprocess.run(
                ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"routing: cannot read git status for {repo}: {exc}")
            return 2
        if status.stdout.strip():
            print(f"routing: {repo} has uncommitted changes:\n{status.stdout}")
            return 2
        prefix = initiative_dir.name
        already = _refuse_if_already_running(runs_dir, prefix)
        if already is not None:
            print(already)
            return 2
        run_id = route.next_run_id([p.name for p in runs_dir.iterdir()], prefix)
        needs = {"initiative": a.initiative, "repo": repo}
        if a.fix_attempts is not None:
            needs["fix_attempts"] = a.fix_attempts
        env_repo = repo
    elif a.graph == "decompose":
        idea_path = Path(a.idea)
        if not idea_path.exists():
            print(f"routing: no idea file at {idea_path}")
            return 2
        workspace_dir = Path(profile["workspace_dir"]).expanduser()
        initiative_dir = workspace_dir / "work" / a.initiative_id
        initiative_md = initiative_dir / "initiative.md"
        idea_fields, idea_body = route.parse_frontmatter(idea_path.read_text(encoding="utf-8"))
        intake = os.path.relpath(idea_path, workspace_dir)
        if initiative_md.exists() and any(initiative_dir.glob("*/*.md")):
            print("routing: initiative.md kept: tickets exist")
        elif initiative_md.exists():
            fields, body = route.parse_frontmatter(initiative_md.read_text(encoding="utf-8"))
            fresh = idea_path.stat().st_mtime <= initiative_md.stat().st_mtime and idea_body == body
            if fresh:
                print(f"routing: {initiative_md} already exists, left untouched")
            elif a.dry_run:
                print(f"dry-run: would write {initiative_md}")
            else:
                content = route.initiative_text(
                    fields.get("id", a.initiative_id), fields.get("title", ""),
                    fields.get("repo", ""), fields.get("intake", intake), idea_body,
                )
                initiative_md.write_text(content, encoding="utf-8")
                print(f"routing: initiative.md refreshed from {idea_path}")
        else:
            initiative_content = route.initiative_text(
                a.initiative_id, idea_fields.get("title", ""), idea_fields.get("repo", ""), intake, idea_body,
            )
            if a.dry_run:
                print(f"dry-run: would write {initiative_md}")
            else:
                initiative_md.parent.mkdir(parents=True, exist_ok=True)
                initiative_md.write_text(initiative_content, encoding="utf-8")
        run_id = route.next_run_id([p.name for p in runs_dir.iterdir()], a.initiative_id)
        needs = {"idea": a.idea, "initiative_id": a.initiative_id}
        env_repo = ""
    else:  # cos
        already = _refuse_if_already_running(runs_dir, "cos")
        if already is not None:
            print(already)
            return 2
        run_id = route.next_run_id([p.name for p in runs_dir.iterdir()], "cos")
        needs = {}
        env_repo = ""

    launch_profile = profile
    overlaid_path = None
    if overlaid_provider_profile is not None:
        overlaid_path = runs_dir / f"{run_id}.provider-profile.yaml"
        launch_profile = {**profile, "provider_profile": str(overlaid_path)}
    argv = route.harness_argv(launch_profile, a.graph, run_id, **needs)
    log_path = runs_dir / f"{run_id}.log"
    pid_path = runs_dir / f"{run_id}.pid"
    trace_dir = runs_dir / f"{run_id}-trace"
    env = route.child_env(dict(os.environ), harness_dir=harness_dir, repo=env_repo, trace_dir=str(trace_dir))

    if a.dry_run:
        print(f"dry-run: {' '.join(argv)}")
        print(f"pid {pid_path}")
        print(f"log {log_path}")
        print(f"trace {trace_dir}")
        return 0

    if overlaid_path is not None:
        overlaid_path.write_text(yaml.safe_dump(overlaid_provider_profile, sort_keys=True), encoding="utf-8")
        (runs_dir / f"{run_id}.ceiling.json").write_text(
            json.dumps({
                "requested": {"tier": a.tier_ceiling, "effort": a.effort_ceiling},
                "applied": {"tier": a.tier_ceiling, "effort": a.effort_ceiling},
                "profile": str(overlaid_path),
            }, indent=2),
            encoding="utf-8",
        )

    with open(log_path, "ab") as log:
        proc = subprocess.Popen(
            argv, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
        )
    pid_path.write_text(str(proc.pid))
    now = datetime.datetime.now(datetime.UTC)
    lock_record, lock_refuse_rc = _leader_read_or_refuse(runs_dir)
    launched_by = None if lock_refuse_rc is not None else _leader_launched_by(lock_record, _leader_pid_alive(lock_record), now, socket.gethostname(), _leader_heartbeat_minutes())
    launched_payload = {"launched_by": launched_by} if launched_by is not None else {}
    (runs_dir / f"{run_id}.launched.json").write_text(
        json.dumps({**launched_payload, "at": now.isoformat()}), encoding="utf-8",
    )
    print(f"run {run_id}")
    print(f"pid {pid_path}")
    print(f"log {log_path}")
    return 0


def _route_lint(a: argparse.Namespace) -> int:
    """work-shape.md §3: `cox route lint <initiative>`, before dispatch.
    `--repo` falls back to the initiative's own `repo:` frontmatter; only
    when neither names one does the reach rule stand down, and that is
    said once here rather than left for `lint_items` to flag every path.
    """
    initiative_dir = Path(a.initiative_dir)
    items = []
    for path in sorted(initiative_dir.glob("*/*.md")):
        fields, body = route.parse_frontmatter(path.read_text(encoding="utf-8"))
        items.append({
            "task": fields.get("id", path.stem),
            "phase": fields.get("phase", path.parent.name),
            "surfaces": fields.get("surfaces", []),
            "body": body,
            "needs": fields.get("needs", []),
        })
    repo = a.repo
    if not repo:
        init_path = initiative_dir / "initiative.md"
        if init_path.exists():
            init_fields, _ = route.parse_frontmatter(init_path.read_text(encoding="utf-8"))
            repo = init_fields.get("repo")
    if not repo:
        print(f"routing: no repo found for {a.initiative_dir}; reach check skipped")
    problems = route.lint_items(items, repo, ("pytest", "git status", "git diff"))
    for problem in problems:
        print(f"{problem.task} {problem.rule}: {problem.detail} -> {problem.fix}")
    return 2 if any(p.rule in ("reach", "coupling") for p in problems) else 0


def _route_groups(a: argparse.Namespace) -> int:
    """work-shape.md §5: `cox route groups` prints the newest
    `plans/intake-groups/<date>.md` file under the profile's workspace, or
    `no groups filed` when none exist.
    """
    profile_path = _profile_path(a)
    text = _read_text_or_none(profile_path)
    if text is None:
        print(f"routing: no profile at {profile_path}")
        return 2
    try:
        profile = route.parse_profile(text)
    except route.ProfileError as exc:
        print(f"routing: profile unreadable: {exc}")
        return 2
    workspace = profile.get("workspace_dir", "")
    if not workspace:
        print(f"routing: workspace_dir not set in profile {profile_path}")
        return 2
    groups_dir = Path(workspace).expanduser() / "plans" / "intake-groups"
    names = sorted(p.name for p in groups_dir.glob("*.md")) if groups_dir.is_dir() else []
    latest = route.latest_groups_file(names)
    if latest is None:
        print("no groups filed")
        return 0
    print(str(groups_dir / latest))
    return 0


def _route_launch_sweep(a: argparse.Namespace) -> int:
    """work-shape.md §1: no harness graph exists yet, so only `--dry-run` runs."""
    argv = route.build_sweep_argv(a.idea, a.initiative_id, a.label)
    if a.dry_run:
        print(f"dry-run: {' '.join(argv)}")
        return 0
    print("routing: sweep has no harness graph to launch yet; use --dry-run")
    return 2


_CORE_PROBE_SCRIPT = '''
import json, os, sys

cartridges_dir, team, *roots = sys.argv[1:]
out = {"import": None, "load": None, "indexed": {}, "resolved": None, "skill_index": {}, "layers": None}
try:
    from core.cartridge import load
    from core.skills import index_from_roots
except Exception as exc:
    out["import"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(out)); raise SystemExit(0)
try:
    index = index_from_roots(roots)
    out["indexed"] = {root: len(index_from_roots([root])) for root in roots}
    out["skill_index"] = {name: [str(p) for p in paths] for name, paths in index.items()}
except Exception as exc:
    # An empty mapping would read downstream as "no roots configured"; a zero
    # per root fails the skills row honestly, and the cartridge row names why.
    out["indexed"] = {root: 0 for root in roots}
    out["load"] = f"skill index failed: {type(exc).__name__}: {exc}"
    print(json.dumps(out)); raise SystemExit(0)
try:
    out["resolved"] = load(team, cartridges_dir, skill_index=index)
except Exception as exc:
    out["load"] = f"{type(exc).__name__}: {exc}"
try:
    from core.cartridge import layers
    out["layers"] = [[label, resolved] for label, resolved in layers(team, cartridges_dir, skill_index=index)]
except Exception as exc:
    # Never an empty list: a chain one layer short must be visibly unknown,
    # not silently read as "nothing set anything".
    out["layers"] = None
    out["layers_error"] = f"{type(exc).__name__}: {exc}"
overlay_text = os.environ.get("AGENT_TOOLS_OVERLAY_TEXT")
if overlay_text is None:
    out["overlay_errors"] = None
else:
    try:
        from core.cartridge import overlay_errors
        out["overlay_errors"] = overlay_errors(overlay_text)
    except Exception as exc:
        out["overlay_errors"] = [f"{type(exc).__name__}: {exc}"]
print(json.dumps(out))
'''


def _run_core_probe(python_path: str, cartridges_dir: str, team: str, skills_roots: list, raw_roots: list | None = None,
                     overlay_text: str | None = None) -> dict:
    """One `python -c` call into the harness venv (spec: setup doctor). Any
    failure to get parseable JSON back is folded into `core_import` as the
    stderr tail, never raised."""
    env = dict(os.environ)
    if overlay_text is not None:
        env["AGENT_TOOLS_OVERLAY_TEXT"] = overlay_text
    try:
        proc = subprocess.run(
            [python_path, "-c", _CORE_PROBE_SCRIPT, cartridges_dir, team, *skills_roots],
            capture_output=True, text=True, timeout=60, env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"core_import": str(exc)}
    try:
        parsed = json.loads(proc.stdout)
    except ValueError:
        tail = (proc.stderr or "").strip().splitlines()
        return {"core_import": tail[-1] if tail else f"core probe exited {proc.returncode} with no JSON"}
    indexed = parsed.get("indexed", {}) or {}
    if raw_roots and len(raw_roots) == len(skills_roots):
        # The probe saw expanded paths; the core keys its skills row by the
        # profile's own strings, so map the counts back by position.
        indexed = {raw: indexed.get(exp, 0) for raw, exp in zip(raw_roots, skills_roots)}
    facts = {"core_import": parsed.get("import"), "cartridge_load": parsed.get("load"),
             "skill_roots_indexed": indexed, "resolved": parsed.get("resolved"),
             "skill_index": parsed.get("skill_index", {})}
    if "overlay_errors" in parsed:
        facts["overlay_errors"] = parsed["overlay_errors"]
    # Exactly one of `provenance`/`provenance_error` is set below, on every
    # path: a fact dict carrying neither would read as "nothing to report"
    # rather than "the walk never happened", which is the silent-mislabel
    # failure this probe exists to rule out.
    parsed_layers = parsed.get("layers")
    if parsed_layers is None:
        facts["provenance_error"] = parsed.get("layers_error") or "the probe exited before the layer walk"
    elif not parsed_layers:
        facts["provenance_error"] = "the loader returned no layers"
    else:
        try:
            facts["provenance"] = provenance.attribute([(label, resolved) for label, resolved in parsed_layers])
        except Exception as exc:
            facts["provenance_error"] = f"{type(exc).__name__}: {exc}"
    return facts


def _provider_command(text) -> str | None:
    if text is None:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("command:"):
            # A flat YAML scalar may carry a trailing comment (`command: claude  # ...`);
            # the command is the first token, never the comment.
            value = stripped.partition(":")[2].split("#", 1)[0].strip().strip("\"'")
            return value.split()[0] if value else None
    return None


def _provider_facts(provider_profile_path: str) -> dict:
    command = _provider_command(_read_text_or_none(Path(provider_profile_path)))
    if command is None:
        return {"provider_command": None, "provider_on_path": False}
    facts = {"provider_command": command, "provider_on_path": shutil.which(command) is not None}
    if facts["provider_on_path"]:
        try:
            proc = subprocess.run([command, "--version"], capture_output=True, text=True, timeout=20)
            lines = (proc.stdout or proc.stderr or "").strip().splitlines()
            facts["provider_version"] = lines[0] if lines else ""
        except (OSError, subprocess.TimeoutExpired):
            pass
    return facts


def _workspace_facts(workspace_dir: str) -> dict:
    ws = Path(workspace_dir).expanduser()
    return {"workspace_dirs": {name: (ws / name).exists() for name in ("work", "runs", "intake")}}


def _gather_doctor_facts(profile_path: Path, repo: Path) -> dict:
    """Gathers exactly the Facts keys `doctor.checks` reads; never refuses on
    a missing or unparseable profile, since reporting that is the doctor's
    job (unlike `_resolve_profile_or_refuse`, which is for `file`/`launch`)."""
    text = _read_text_or_none(profile_path)
    facts: dict = {"profile_path": str(profile_path), "profile_text": text}
    if text is None:
        return facts
    try:
        profile = route.parse_profile(text)
    except route.ProfileError:
        return facts
    singles = [profile.get(k, "") for k in ("cartridges_dir", "provider_profile", "harness_dir", "workspace_dir")]
    roots = list(profile.get("skills_roots") or [])
    # Every profile path is expanded ONCE, here; `paths_exist` keeps the raw
    # strings as keys (doctor.py's contract) but tests the expanded path.
    expand = lambda p: str(Path(p).expanduser()) if p else ""  # noqa: E731
    facts["paths_exist"] = {p: Path(expand(p)).exists() for p in (*singles, *roots) if p}
    harness_dir = expand(profile.get("harness_dir", ""))
    venv_python = Path(harness_dir) / ".venv" / "bin" / "python" if harness_dir else Path("/nonexistent")
    facts["harness_python_exists"] = venv_python.exists()
    if facts["harness_python_exists"]:
        overlay_text = _read_text_or_none(repo / ".agent" / "cartridge.yaml")
        facts.update(_run_core_probe(str(venv_python), expand(profile.get("cartridges_dir", "")), profile.get("team", ""),
                                     [expand(r) for r in roots], raw_roots=roots, overlay_text=overlay_text))
    if profile.get("provider_profile"):
        facts.update(_provider_facts(expand(profile["provider_profile"])))
    if profile.get("workspace_dir"):
        facts.update(_workspace_facts(expand(profile["workspace_dir"])))
    facts["schema_versions"] = _schema_versions(harness_dir, expand(profile.get("provider_profile", "")),
                                                 [expand(r) for r in roots])
    return facts


def _setup_doctor(a: argparse.Namespace) -> int:
    repo = Path(a.repo).expanduser() if a.repo else Path.cwd()
    facts = _gather_doctor_facts(_profile_path(a), repo)
    rows = doctor.checks(facts)
    rc = doctor.exit_code(rows)
    print(json.dumps({"rows": rows, "ok": rc == 0}, indent=2) if a.json else doctor.render(rows))
    return rc


def _install_facts(a: argparse.Namespace) -> dict:
    """Gathers exactly what `install_plan` needs and nothing it decides:
    PATH lookups, each repo's venv, the profile's existence, and the
    config/settings paths expanded once, here."""
    root = a.root
    config_dir = str(Path("~/.config").expanduser())
    claude_settings_path = str(Path("~/.claude/settings.json").expanduser())
    python_exists = {repo: (Path(root) / repo / ".venv" / "bin" / "python").exists()
                      for repo in setup_install.REPOS}
    return {
        "root": root,
        "team": a.team,
        "workspace": a.workspace,
        "provider_profile": a.provider_profile or f"{root}/coxswain-cartridges/providers/claude-code.yaml",
        "skills_root": a.skills_root or f"{root}/coxswain-cartridges/skills-plugins",
        "uv_on_path": shutil.which("uv") is not None,
        "python_exists": python_exists,
        "claude_on_path": shutil.which("claude") is not None,
        "profile_exists": Path(setup_install.profile_path(config_dir)).exists(),
        "force_profile": a.force_profile,
        "plugins": a.plugins,
        "hook": a.hook,
        "config_dir": config_dir,
        "claude_settings_path": claude_settings_path,
        "assume": a.assume,
        "window_ceiling_usd": a.window_ceiling_usd,
        "weekly_ceiling_usd": a.weekly_ceiling_usd,
    }


def _install_run(step: dict) -> str | None:
    argv = " ".join(step["argv"])
    try:
        proc = subprocess.run(step["argv"], cwd=step.get("cwd"), capture_output=True, text=True)
    except OSError as exc:
        print(f"FAILED: {argv}: {exc}")
        return f"{argv}: {exc}"
    if proc.returncode == 0:
        print(f"run: {argv}")
        return None
    tail = "\n".join(proc.stderr.strip().splitlines()[-5:])
    if step.get("warn_only"):
        print(f"warn: {argv} exited {proc.returncode}: {tail}")
        return None
    print(f"FAILED: {argv} exited {proc.returncode}")
    print(tail)
    return f"{argv} exited {proc.returncode}"


def _execute_step(step: dict) -> str | None:
    op = step["op"]
    if op == "run":
        return _install_run(step)
    if op == "write":
        path = Path(step["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(step["text"], encoding="utf-8")
        print(f"write {path}")
        return None
    if op == "skip":
        print(f"skip {step['what']} — {step['why']}")
        return None
    if op == "hook":
        path = Path(step["path"])
        try:
            existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except json.JSONDecodeError as exc:
            print(f"FAILED: {path} is not valid JSON: {exc}")
            return f"{path} is not valid JSON: {exc}"
        new_settings, changed = setup_install.hook_settings(existing)
        if changed:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(new_settings, indent=2) + "\n", encoding="utf-8")
            print(f"hook added to {path}")
        else:
            print(f"hook already present in {path}")
        return None
    if op == "print":
        print(step["text"])
        return None
    return f"unknown step op: {op!r}"


def _setup_install(a: argparse.Namespace) -> int:
    try:
        steps = setup_install.install_plan(**_install_facts(a))
    except ValueError as exc:
        print(f"refusing: {exc}")
        return 2
    if a.dry_run:
        print(setup_install.render_plan(steps))
        return 0
    for step in steps:
        failure = _execute_step(step)
        if failure is not None:
            return 1
    return 0


def _setup_fields(a: argparse.Namespace) -> tuple[str, str, str]:
    """(root, team, workspace) to prefill the TUI with, from the profile
    when it resolves, else empty strings — never a hard refusal here."""
    text = _read_text_or_none(_profile_path(a))
    if text is None:
        return "", "", ""
    try:
        profile = route.parse_profile(text)
    except route.ProfileError:
        return "", "", ""
    harness_dir = profile.get("harness_dir", "")
    root = str(Path(harness_dir).parent) if harness_dir else ""
    return root, profile.get("team", ""), profile.get("workspace_dir", "")


def _load_manifest(path: Path) -> dict | None:
    """The parsed manifest, or None on a missing or unparseable file — never
    a traceback; the caller turns None into a named, exit-2 refusal."""
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None


def _checkout_facts(path: Path, fetch: bool = False) -> dict:
    """A component's checkout state. Present only when `<path>/.git`
    exists: `git -C` walks up to the nearest enclosing repository, so a
    bare directory — or one merely nested inside some other checkout —
    must never borrow that repository's tag or dirty state."""
    if not (path / ".git").exists():
        return {"present": False, "tag": None, "dirty": False}
    describe = subprocess.run(["git", "-C", str(path), "describe", "--tags", "--exact-match"],
                               capture_output=True, text=True)
    tag = describe.stdout.strip() if describe.returncode == 0 else None
    status = subprocess.run(["git", "-C", str(path), "status", "--porcelain"], capture_output=True, text=True)
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)

    branch = git("branch", "--show-current").stdout.strip()
    fresh = not fetch or (bool(branch) and git("fetch", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}").returncode == 0)
    tip = git("rev-parse", f"origin/{branch}")
    at_tip = bool(branch) and fresh and tip.returncode == 0 and tip.stdout.strip() == git("rev-parse", "HEAD").stdout.strip()
    return {"present": True, "tag": tag, "dirty": bool(status.stdout.strip()), "branch": branch or None,
            "branch_tip": at_tip}


def _gather_checkout_facts(root: Path, components: dict, fetch: bool = False) -> dict:
    """Every manifest component, plus any other git checkout actually
    present under `root` — the `extra` rows `install.rows` can then report.
    `fetch` refreshes each declared checkout's remote branch first (edge)."""
    options = {"fetch": True} if fetch else {}
    declared = {name: _checkout_facts(root / name, **options) for name in components}
    if not root.exists():
        return declared
    undeclared = {p.name for p in root.iterdir()
                  if p.is_dir() and p.name not in components and (p / ".git").exists()}
    return {**declared, **{name: _checkout_facts(root / name) for name in undeclared}}


def _manifest_provider_command(manifest: dict, provider: str) -> str:
    """The executable to look up on PATH: the manifest's own `command` for
    this provider when it names one, else the provider key itself."""
    return manifest.get("providers", {}).get(provider, {}).get("command", provider)


def _real_run(argv: list, cwd: str | None) -> tuple:
    """The subprocess wrapper `install_exec.execute` calls at the edge;
    stdout and stderr are folded together since the caller only prints."""
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


def _manifest_at_version(manifest: dict, to: str | None) -> dict:
    """The manifest as given, unless `to` names a version: then every
    component's `tag` is overridden to `to`, so `--to VERSION` reaches
    `install.plan` as the pin every component is judged and cloned
    against, rather than being accepted and silently ignored."""
    if to is None:
        return manifest
    components = {name: {**spec, "tag": to} for name, spec in manifest.get("components", {}).items()}
    return {**manifest, "components": components}


def _install_execute(steps: list, manifest: dict, options: dict, root: Path) -> int:
    """Runs a planned install/upgrade for real: enrich the planner's steps
    with the repo/tag/team/workspace `install_exec` needs, execute them,
    report one ok/FAILED/skipped/refused line per step, then the versions
    table built from checkouts gathered again after execution — the facts
    gathered before a single step ran are not evidence of what landed.
    A `refuse` step is reported as `refused`, not `FAILED` — it never
    called `run` and its non-zero exit is a decision, not a command that
    broke. Exits 0 only when every step that ran, ran clean."""
    exec_steps = install_exec.from_plan(steps, manifest=manifest, options=options)
    results = install_exec.execute(exec_steps, root=str(root), run=_real_run)
    for result in results:
        step = result["step"]
        label = f"{step['kind']} {step.get('component', '')}".strip()
        if step["kind"] == "skip" or result["exit"] is None:
            print(f"skipped: {label}")
        elif step["kind"] == "refuse":
            print(f"refused: {label}: {result['output']}")
        elif result["exit"] == 0:
            print(f"ok: {label}")
        else:
            print(f"FAILED: {label}")
            for line in (result["output"] or "").rstrip().splitlines():
                print(f"    {line}")
    post_facts = {"root": str(root), "checkouts": _gather_checkout_facts(root, manifest.get("components", {}))}
    display = [{"component": c, "pinned_tag": p or "", "installed_tag": i or "", "status": s}
               for c, p, i, s in install.rows(manifest, post_facts, options.get("channel", "release"))]
    print(records.format_table(display, ["component", "pinned_tag", "installed_tag", "status"]))
    return 0 if all(result["exit"] in (0, None) for result in results) else 2


def _install(a: argparse.Namespace) -> int:
    manifest_path = Path(a.manifest) if a.manifest else Path(a.root) / "coxswain" / "manifest.toml"
    manifest = _load_manifest(manifest_path)
    if manifest is None:
        print(f"refusing: no manifest at {manifest_path}")
        return 2
    root = Path(a.root)
    channel = "edge" if a.edge else "release"
    facts = {
        "root": str(root),
        "checkouts": _gather_checkout_facts(root, manifest.get("components", {}), fetch=a.edge),
        "provider_cli_on_path": shutil.which(_manifest_provider_command(manifest, a.provider)) is not None,
    }
    options = {"provider": a.provider, "with": a.with_ or [], "root": str(root), "team": a.team,
               "workspace": a.workspace, "channel": channel}
    schema_state, schema_detail = schema.status(_schema_versions(str(root / "harness"), str(root / "cartridges")))
    if schema_state != "ok":
        print(f"schema  WARN  {schema_detail}")
    steps = install.plan(manifest, facts, options)
    print(f"channel: {channel}")
    for step in steps:
        print(f"{step['kind']} {step['component']}: {step['detail']}")
    if a.dry_run:
        return 2 if any(step["kind"] == "refuse" for step in steps) else 0
    return _install_execute(steps, manifest, options, root)


def _upgrade(a: argparse.Namespace) -> int:
    manifest_path = Path(a.manifest) if a.manifest else Path(a.root) / "coxswain" / "manifest.toml"
    manifest = _load_manifest(manifest_path)
    if manifest is None:
        print(f"refusing: no manifest at {manifest_path}")
        return 2
    manifest = _manifest_at_version(manifest, a.to)
    root = Path(a.root)
    facts = {
        "root": str(root),
        "checkouts": _gather_checkout_facts(root, manifest.get("components", {})),
        "provider_cli_on_path": shutil.which(_manifest_provider_command(manifest, a.provider)) is not None,
    }
    dirty = sorted(name for name, checkout in facts["checkouts"].items() if checkout.get("dirty"))
    if dirty:
        print(f"refusing: {root / dirty[0]} is dirty; refusing to upgrade it")
        return 2
    options = {"provider": a.provider, "with": a.with_ or [], "root": str(root), "team": a.team, "workspace": a.workspace}
    steps = install.plan(manifest, facts, options)
    for step in steps:
        print(f"{step['kind']} {step['component']}: {step['detail']}")
    if a.dry_run:
        return 2 if any(step["kind"] == "refuse" for step in steps) else 0
    return _install_execute(steps, manifest, options, root)


def _schema_versions(harness_dir: str = "", provider_profile: str = "", skills_roots: list[str] | None = None
                      ) -> dict[str, str | None]:
    """The schema each of the three tools was built against, read from the
    profile's (or `--root`'s) checkouts rather than imported, since graphs
    and cartridges are not installed in this venv."""
    return {
        "cartridges": schema.cartridges_schema(provider_profile, skills_roots or []),
        "graphs": schema.graphs_schema(harness_dir),
        "tools": schema.TOOLS_SCHEMA,
    }


def _versions(a: argparse.Namespace) -> int:
    manifest_path = Path(a.manifest) if a.manifest else Path(a.root or ".") / "coxswain" / "manifest.toml"
    manifest = _load_manifest(manifest_path)
    if manifest is None:
        print(f"refusing: no manifest at {manifest_path}")
        return 2
    root = Path(a.root) if a.root else manifest_path.resolve().parent.parent
    facts = {"root": str(root), "checkouts": _gather_checkout_facts(root, manifest.get("components", {})),
              "provider_cli_on_path": False}
    schema_versions = _schema_versions(str(root / "harness"), str(root / "cartridges"))
    display = [{"component": c, "pinned_tag": p or "", "installed_tag": i or "", "status": s,
                "schema": schema.cell(schema_versions.get(c)) if c in schema_versions else "?"}
               for c, p, i, s in install.rows(manifest, facts)]
    print(records.format_table(display, ["component", "pinned_tag", "installed_tag", "status", "schema"]))
    return 0


def _remote_tags(repo: str) -> list[str] | None:
    """The tags on `repo`'s GitHub remote, or None when git could not read the
    remote. None is not `[]`: the planner refuses on None, so an unreachable or
    misnamed remote can never look like a clean one."""
    result = subprocess.run(["git", "ls-remote", "--tags", f"https://github.com/{repo}.git"],
                             capture_output=True, text=True)
    return None if result.returncode != 0 else release.parse_ls_remote(result.stdout)


_RELEASE_DETAIL = {
    "refuse": lambda step: step["detail"],
    "note": lambda step: step["detail"],
    "tag": lambda step: f"{step['repo']} -> {step['tag']}",
    "bump_manifest": lambda step: f"{step['from']} -> {step['to']}",
    "notes": lambda step: step["path"],
    "tag_self": lambda step: step["tag"],
    "wait_workflows": lambda step: step["tag"],
    "pinned": lambda step: step["tag"],
    "tap_formula_pr": lambda step: f"{step['repo']} {step['path']} from {step['index_url']}",
    "rejoin": lambda step: f"{step['from']} -> {step['tag']} ({step['commits']} commits)",
    "github_release": lambda step: (f"{step['repo']} -> "
        f"{' '.join(release.github_release_create_argv(step['tag'], step['title'], step['notes_path']))}"),
}


def _release_detail(step: dict) -> str:
    """One line of detail per step kind; an unknown kind shows itself rather than raising."""
    fmt = _RELEASE_DETAIL.get(step["kind"])
    return fmt(step) if fmt is not None else str(step)


def _default_branch(directory: str, run) -> str:
    """`directory`'s default branch, read from `origin/HEAD` — `"main"` when
    that symbolic ref can't be read (a plain checkout with no such ref set,
    or a directory the fake runner in tests never populated one for)."""
    ref_rc, ref_out = run(["git", "-C", directory, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], None)
    return ref_out.strip().rsplit("/", 1)[-1] if ref_rc == 0 and ref_out.strip() else "main"


def _checkout_ready(directory: str, run) -> tuple[bool, str]:
    """Clean and on its default branch, or `(False, reason)` — checked
    before a single tag is made, since a release must never tag some
    components and stop partway through a checkout that turns out dirty."""
    status_rc, status_out = run(["git", "-C", directory, "status", "--porcelain"], None)
    if status_rc != 0:
        return False, status_out.strip() or f"could not read status for {directory}"
    if status_out.strip():
        return False, f"{directory} is dirty"
    _, branch_out = run(["git", "-C", directory, "rev-parse", "--abbrev-ref", "HEAD"], None)
    current = branch_out.strip()
    default = _default_branch(directory, run)
    if current != default:
        return False, f"{directory} is on {current}, not {default}"
    return True, ""


def _fetch_index(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


def _release_step_dir(component: str, root: str, overrides: dict, umbrella: str) -> str:
    """The checkout a `bump_pyproject`/`push`/`pr_create`/`wait_checks`/`merge`
    step runs against: `umbrella` for the manifest's own bump (its component
    is always `"manifest"`, never a real manifest entry), otherwise that
    component's own checkout."""
    return umbrella if component == "manifest" else release.component_dir(root, component, overrides)


def _release_bump(directory: str, branch: str, commit_subject: str, paths: list[str], write, run) -> tuple[bool, str]:
    """Checks out `branch` from the default branch in `directory`, and only
    once that succeeds calls `write()` to rewrite `paths` on disk — `write`
    never runs, and nothing on the default branch is ever touched, when the
    checkout itself fails (a stale local `branch` left by an earlier partial
    run, a hook, a permission error). Then stages `paths` and commits them
    with `commit_subject`. `(False, detail)` names the first git call that
    failed."""
    co_rc, co_out = run(release.checkout_branch_argv(directory, branch), None)
    if co_rc != 0:
        return False, co_out.strip() or "checkout failed"
    write()
    add_rc, add_out = run(release.add_argv(directory, *paths), None)
    if add_rc != 0:
        return False, add_out.strip() or "add failed"
    commit_rc, commit_out = run(release.commit_argv(directory, commit_subject, *paths), None)
    if commit_rc != 0:
        return False, commit_out.strip() or "commit failed"
    return True, "committed"


def _release_bump_pyproject(directory: str, path: Path, to: str, branch: str, commit_subject: str,
                             run) -> tuple[bool, str]:
    """`_release_bump` for a `bump_pyproject` step: rewrites `path`'s version
    line to `to`, only once `branch` is checked out."""
    def write():
        path.write_text(release.bumped_version_line(path.read_text(), to))
    return _release_bump(directory, branch, commit_subject, ["pyproject.toml"], write, run)


def _release_bump_manifest(umbrella: str, manifest_file: Path, to: str, branch: str, commit_subject: str,
                            rejoining: set, run) -> tuple[bool, str]:
    """`_release_bump` for a `bump_manifest` step: rewrites `manifest_file`'s
    version and component tags, and — when the umbrella checkout carries its
    own `pyproject.toml` — that file's version line and, when `uv.lock` also
    exists and that pyproject names a package, its matching `[[package]]`
    stanza's version — all only once `branch` is checked out. Which of the
    umbrella's files exist is read before the checkout (a read touches
    nothing), so `paths` is known up front without ever writing early."""
    pyproject_path = Path(umbrella) / "pyproject.toml"
    lock_path = Path(umbrella) / "uv.lock"
    paths = ["manifest.toml"]
    package_name = None
    if pyproject_path.exists():
        package_name = tomllib.loads(pyproject_path.read_text()).get("project", {}).get("name")
        paths.append("pyproject.toml")
        if lock_path.exists() and package_name:
            paths.append("uv.lock")

    def write():
        manifest_file.write_text(release.bumped_manifest_text(manifest_file.read_text(), to, rejoining=rejoining))
        if "pyproject.toml" in paths:
            pyproject_path.write_text(release.bumped_version_line(pyproject_path.read_text(), to))
        if "uv.lock" in paths:
            lock_path.write_text(release.bumped_uv_lock_text(lock_path.read_text(), package_name, to))

    return _release_bump(umbrella, branch, commit_subject, paths, write, run)


def _component_declares_tag_trigger(directory: str) -> bool:
    """True when any `.github/workflows/*.y*ml` file under `directory`
    triggers on a pushed tag — read at the edge, decided by the pure
    `release.declares_tag_trigger`."""
    workflows_dir = Path(directory) / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return False
    paths = sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))
    return any(release.declares_tag_trigger(p.read_text(encoding="utf-8")) for p in paths)


def _wait_workflows(directory: str, tag: str, component: str, run,
                     timeout_s: float = 900, sleep=time.sleep, now=time.monotonic) -> tuple[bool, str]:
    """A component with no tag-triggered workflow has nothing to wait on and
    returns immediately, before ever polling — otherwise it would stall for
    the full `timeout_s` on every release. Otherwise polls `gh run list
    --branch <tag> --json status,conclusion,name,url,event,headBranch`,
    keeping only the runs the tag itself started — event `push` on that
    exact `headBranch` — until every one has concluded or `timeout_s`
    passes; zero matching runs inside that loop is always still pending.
    Any run whose conclusion is not `success` after that fails, naming
    `component`, that run's `name` and its `url`; so does a timeout with
    zero matching runs, since a declared trigger means one was expected."""
    if not _component_declares_tag_trigger(directory):
        return True, "no tag-triggered workflow for this component"
    started = now()
    while True:
        # `gh` infers the repository from its working directory; from the
        # release root (not a git repository) it fails before asking GitHub —
        # the first real 0.7.0 cut stopped on exactly that after tagging one component.
        gh_rc, gh_out = run(["gh", "run", "list", "--branch", tag, "--json",
                              "status,conclusion,name,url,event,headBranch"], directory)
        if gh_rc != 0:
            return False, gh_out.strip() or "gh run list failed"
        all_runs = json.loads(gh_out) if gh_out.strip() else []
        runs = [r for r in all_runs if r.get("event") == "push" and r.get("headBranch") == tag]
        pending = any(r.get("status") != "completed" for r in runs)
        timed_out = now() - started >= timeout_s
        if (not runs or pending) and not timed_out:
            sleep(10)
            continue
        if not runs:
            return False, f"{component}: no workflow run started for {tag} within {timeout_s:.0f}s"
        failed = next((r for r in runs if r.get("conclusion") != "success"), None)
        if failed is not None:
            return False, f"{component}: {failed.get('name')} did not succeed ({failed.get('url')})"
        return True, f"{len(runs)} run(s) green for {tag}"


def _previous_release_tag(umbrella: str, name: str, version: str, run) -> str | None:
    """`name`'s tag in the manifest of the release before `version` — the
    latest `docs/releases/*.md` stem below `version`, read via `git show
    v<previous>:manifest.toml` through the injected `run` — or None when
    `version` is the first release this umbrella has notes for."""
    releases_dir = Path(umbrella) / "docs" / "releases"
    versions = release.versions_oldest_first(p.stem for p in releases_dir.glob("*.md") if p.stem != "index")
    earlier = [v for v in versions if v != version]
    if not earlier:
        return None
    show_rc, show_out = run(["git", "-C", umbrella, "show", f"v{earlier[-1]}:manifest.toml"], None)
    if show_rc != 0:
        return None
    return tomllib.loads(show_out).get("components", {}).get(name, {}).get("tag")


def _github_release_notes_text(umbrella: str, notes_path: str, heading: str, from_tag: str | None,
                                link: str | None) -> str:
    """The `github_release` step's own body for a component or crew section:
    `notes_path`'s text under `heading`, `unchanged since <from_tag>` when
    that section is absent and a previous release named a tag, or `first
    release` when none did — followed by the link line back to the umbrella
    release. The backfill command builds every body it creates or edits
    through this same function, so a rerun's comparison is apples to apples."""
    section = release.extract_release_notes(str(Path(umbrella) / notes_path), heading)
    fallback = f"unchanged since {from_tag}\n" if from_tag else "first release\n"
    body = section if section is not None else fallback
    link_line = f"\nSee the full release notes: {link}\n" if link else "\n"
    return body + link_line


def _release_execute(steps: list[dict], version: str, root: str, overrides: dict, umbrella: str, run,
                      manifest: dict, manifest_path: str, sleep=time.sleep, now=time.monotonic,
                      fetch_index=_fetch_index) -> int:
    """Runs `steps` for real, through `run`. Every checkout that will be
    tagged or branched — every component, the umbrella when `tag_self` is in
    the plan, and any component or the umbrella a `bump_pyproject` or
    `bump_manifest` step will branch — and the umbrella's release note are
    all checked before a single tag is made. Then each `tag` step's tag and
    push run in turn, and `tag_self` tags and pushes the umbrella; a
    `pinned` step runs no git command at all, since its component keeps the
    tag the manifest already names. A `bump_pyproject`/`bump_manifest` step
    checks out its `branch`, rewrites the version on disk and commits it —
    a no-op, printing "already at <version>", when the file already reads
    `to`, and every later `push`/`pr_create`/`wait_checks`/`merge` step for
    that same component no-ops the same way rather than push a branch
    nothing was committed to. `merge` squash-merges and, once that lands,
    switches the checkout back to its default branch and pulls it — the very
    next step for that same directory is the pre-existing `tag`/`tag_self`
    executor, which tags HEAD with no ref, so without this the tag would
    land on the stale pre-squash commit `bump_pyproject`/`bump_manifest`
    left checked out on `release/<version>` instead of what actually merged.
    A `github_release` step checks `gh release view` first and edits an
    existing release instead of creating one. One line per step; the first
    failure stops the rest."""
    refusal = next((s for s in steps if s["kind"] == "refuse"), None)
    if refusal is not None:
        print(f"refuse {refusal['component']}: {refusal['detail']}")
        return 2

    tag_checkouts = [(s["component"], release.component_dir(root, s["component"], overrides))
                      for s in steps if s["kind"] in ("tag", "rejoin")]
    umbrella_checkouts = [("coxswain", umbrella)] if any(s["kind"] == "tag_self" for s in steps) else []
    for name, directory in tag_checkouts + umbrella_checkouts:
        ready, reason = _checkout_ready(directory, run)
        if not ready:
            print(f"refuse {name}: {reason}")
            return 2

    for notes_step in (s for s in steps if s["kind"] == "notes"):
        if not (Path(umbrella) / notes_step["path"]).exists():
            print(f"refuse notes: {notes_step['path']} missing under {umbrella}")
            return 2

    # A component whose bump_pyproject/bump_manifest step no-ops (the file was
    # already at the target version) has nothing to push, PR, wait on or
    # merge; its later land steps no-op the same way rather than push a
    # branch nothing was ever committed to.
    already_bumped: dict[str, str] = {}

    for step in steps:
        kind = step["kind"]
        if kind in ("tag", "rejoin"):
            directory = release.component_dir(root, step["component"], overrides)
            tag_rc, tag_out = run(release.tag_argv(directory, version), None)
            if tag_rc != 0:
                print(f"FAILED tag {step['component']}: {tag_out.strip()}")
                return 2
            push_rc, push_out = run(release.push_argv(directory, version), None)
            if push_rc != 0:
                print(f"FAILED push {step['component']}: {push_out.strip()}")
                return 2
            print(f"{kind} {step['component']}: {step['tag']}")
        elif kind == "notes":
            index_path = Path(umbrella) / "docs" / "releases" / "index.md"
            existing = index_path.read_text() if index_path.exists() else ""
            index_path.write_text(release.release_index_text(existing, version, manifest))
            print(f"notes notes: {step['path']}")
        elif kind == "note":
            print(f"note {step['component']}: {step['detail']}")
        elif kind == "pinned":
            print(f"pinned {step['component']}: {step['tag']}")
        elif kind == "bump_pyproject":
            directory = release.component_dir(root, step["component"], overrides)
            path = Path(directory) / "pyproject.toml"
            if release.component_version(path.read_text()) == step["to"]:
                already_bumped[step["component"]] = step["to"]
                print(f"bump_pyproject {step['component']}: already at {step['to']}")
                continue
            ok, detail = _release_bump_pyproject(directory, path, step["to"], step["branch"],
                                                  step["commit_subject"], run)
            if not ok:
                print(f"FAILED bump_pyproject {step['component']}: {detail}")
                return 2
            print(f"bump_pyproject {step['component']}: {step['from']} -> {step['to']}")
        elif kind == "bump_manifest":
            manifest_file = Path(manifest_path)
            if tomllib.loads(manifest_file.read_text()).get("coxswain", {}).get("version") == step["to"]:
                already_bumped[step["component"]] = step["to"]
                print(f"bump_manifest {step['component']}: already at {step['to']}")
                continue
            ok, detail = _release_bump_manifest(umbrella, manifest_file, step["to"], step["branch"],
                                                 step["commit_subject"], release.rejoined(steps), run)
            if not ok:
                print(f"FAILED bump_manifest {step['component']}: {detail}")
                return 2
            print(f"bump_manifest {step['component']}: {step['from']} -> {step['to']}")
        elif kind == "push":
            if step["component"] in already_bumped:
                print(f"push {step['component']}: already at {already_bumped[step['component']]}")
                continue
            directory = _release_step_dir(step["component"], root, overrides, umbrella)
            push_rc, push_out = run(release.push_branch_argv(directory, step["branch"]), None)
            if push_rc != 0:
                print(f"FAILED push {step['component']}: {push_out.strip()}")
                return 2
            print(f"push {step['component']}: {step['branch']}")
        elif kind == "pr_create":
            if step["component"] in already_bumped:
                print(f"pr_create {step['component']}: already at {already_bumped[step['component']]}")
                continue
            directory = _release_step_dir(step["component"], root, overrides, umbrella)
            pr_rc, pr_out = run(release.pr_create_argv(step["title"], step["body"]), directory)
            if pr_rc != 0:
                print(f"FAILED pr_create {step['component']}: {pr_out.strip()}")
                return 2
            print(f"pr_create {step['component']}: {step['title']}")
        elif kind == "wait_checks":
            if step["component"] in already_bumped:
                print(f"wait_checks {step['component']}: already at {already_bumped[step['component']]}")
                continue
            directory = _release_step_dir(step["component"], root, overrides, umbrella)
            wc_ok, wc_out = _await_checks(lambda d=directory: run(release.pr_checks_argv(), d), sleep=sleep, now=now)
            if not wc_ok:
                print(f"FAILED wait_checks {step['component']}: {wc_out}")
                return 2
            print(f"wait_checks {step['component']}: {wc_out}")
        elif kind == "merge":
            if step["component"] in already_bumped:
                print(f"merge {step['component']}: already at {already_bumped[step['component']]}")
                continue
            directory = _release_step_dir(step["component"], root, overrides, umbrella)
            merge_rc, merge_out = run(release.pr_merge_argv(), directory)
            if merge_rc != 0:
                print(f"FAILED merge {step['component']}: {merge_out.strip()}")
                return 2
            # `gh pr merge --squash` lands the bump as a brand-new commit on
            # the default branch upstream; it does not touch this local
            # checkout, which `bump_pyproject`/`bump_manifest` left on
            # `release/<version>`. The very next step for this same directory
            # is the pre-existing `tag`/`tag_self` executor, which tags HEAD
            # with no ref — so without switching back and pulling here, it
            # would tag the stale pre-squash commit on the release branch,
            # not what actually landed, and leave the checkout parked off
            # the default branch for the next release to refuse.
            default = _default_branch(directory, run)
            co_rc, co_out = run(release.checkout_ref_argv(directory, default), None)
            if co_rc != 0:
                print(f"FAILED merge {step['component']}: {co_out.strip()}")
                return 2
            pull_rc, pull_out = run(release.pull_argv(directory, default), None)
            if pull_rc != 0:
                print(f"FAILED merge {step['component']}: {pull_out.strip()}")
                return 2
            print(f"merge {step['component']}: merged")
        elif kind == "tag_self":
            self_tag_rc, self_tag_out = run(release.tag_argv(umbrella, version), None)
            if self_tag_rc != 0:
                print(f"FAILED tag_self coxswain: {self_tag_out.strip()}")
                return 2
            self_push_rc, self_push_out = run(release.push_argv(umbrella, version), None)
            if self_push_rc != 0:
                print(f"FAILED push coxswain: {self_push_out.strip()}")
                return 2
            print(f"tag_self coxswain: {step['tag']}")
        elif kind == "wait_workflows":
            directory = umbrella if step["component"] == "coxswain" else release.component_dir(root, step["component"], overrides)
            ok, detail = _wait_workflows(directory, step["tag"], step["component"], run)
            if not ok:
                # docs/design/release-discipline.md §2 says exit code 1; every
                # other failure branch in this function returns 2, and that
                # file-wide convention wins here for consistency with its siblings.
                print(f"FAILED wait_workflows {step['component']}: {detail}")
                return 2
            print(f"wait_workflows {step['component']}: {detail}")
        elif kind == "github_release":
            directory = umbrella if step["component"] == "coxswain" else release.component_dir(root, step["component"], overrides)
            if step["heading"] is None:
                notes_path = str(Path(umbrella) / step["notes_path"])
            else:
                bumped = any(s["kind"] == "tag" and s["component"] == step["component"] for s in steps)
                from_tag = (_previous_release_tag(umbrella, step["component"], version, run)
                            if bumped else step.get("from"))
                text = _github_release_notes_text(umbrella, step["notes_path"], step["heading"], from_tag, step.get("link"))
                with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tmp:
                    tmp.write(text)
                notes_path = tmp.name
            view_rc, _ = run(release.github_release_view_argv(step["tag"]), directory)
            argv = (release.github_release_edit_argv(step["tag"], notes_path) if view_rc == 0 else
                    release.github_release_create_argv(step["tag"], step["title"], notes_path))
            gr_rc, gr_out = run(argv, directory)
            if gr_rc != 0:
                print(f"FAILED github_release {step['component']}: {gr_out.strip()}")
                return 2
            print(f"github_release {step['component']}: {step['tag']}")
        elif kind == "tap_formula_pr":
            try:
                sdist = release.sdist_from_index(fetch_index(step["index_url"]))
            except (OSError, ValueError) as exc:
                sdist, detail = None, str(exc)
            else:
                detail = "the index lists no sdist"
            if sdist is None:
                print(f"FAILED tap_formula_pr tap: {step['index_url']}: {detail}")
                return 2
            directory = release.component_dir(root, release.TAP_CHECKOUT, overrides)
            formula = Path(directory) / step["path"]
            ok, detail = _release_bump(
                directory, step["branch"], step["title"], [step["path"]],
                lambda f=formula, s=sdist: f.write_text(release.bumped_formula_text(f.read_text(), version, *s)), run)
            if not ok:
                print(f"FAILED tap_formula_pr tap: {detail}")
                return 2
            for argv, cwd in ((release.push_branch_argv(directory, step["branch"]), None),
                              (release.pr_create_argv(step["title"], f"Bumps the formula to {version} on PyPI."), directory)):
                rc, out = run(argv, cwd)
                if rc != 0:
                    print(f"FAILED tap_formula_pr tap: {out.strip()}")
                    return 2
            print(f"tap_formula_pr tap: {step['title']}")
        else:
            print(f"FAILED {kind} {step.get('component', '')}: no executor for this step kind")
            return 2
    return 0


def _maintainer_remote_url(directory: str) -> str | None:
    """`git -C <directory> remote get-url origin`, or None when git could
    not read it — an unreadable remote is not a maintainer's checkout."""
    result = subprocess.run(["git", "-C", directory, "remote", "get-url", "origin"],
                             capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def _tools_repository_url() -> str | None:
    """coxswain-tools' own `Repository` project URL, from installed package
    metadata — a live read that survives shipping as a wheel, unlike a
    `pyproject.toml` path that only resolves in a checkout. `None` when the
    package (or that URL) can't be found, e.g. an uninstalled checkout."""
    try:
        urls = importlib.metadata.metadata("coxswain-tools").get_all("Project-URL") or []
    except importlib.metadata.PackageNotFoundError:
        return None
    for entry in urls:
        name, _, url = entry.partition(",")
        if name.strip() == "Repository":
            return url.strip()
    return None


def _tap_state(directory: str) -> str:
    """`clean`, `dirty` or `absent`; a checkout whose status git cannot read is `absent`."""
    if not (Path(directory) / ".git").exists():
        return "absent"
    result = subprocess.run(["git", "-C", directory, "status", "--porcelain"], capture_output=True, text=True)
    return "absent" if result.returncode != 0 else "dirty" if result.stdout.strip() else "clean"


def _release_moved(a: argparse.Namespace) -> int:
    print("moved: use cox dev release")
    return 2


def _release(a: argparse.Namespace) -> int:
    manifest_path = Path(a.manifest) if a.manifest else Path("coxswain") / "manifest.toml"
    manifest = _load_manifest(manifest_path)
    if manifest is None:
        print(f"refusing: no manifest at {manifest_path}")
        return 2
    checkout = str(manifest_path.resolve().parent)
    remote = _maintainer_remote_url(checkout)
    if remote is None or not release.is_maintainer_remote(remote):
        print(f"refuse: {checkout} is not a ppfenning/coxswain checkout (cox dev release runs on a maintainer's machine)")
        return 2
    root = a.root or "."
    overrides = dict(pair.split("=", 1) for pair in (a.checkout or []))
    component_dirs = {name: release.component_dir(root, name, overrides) for name in manifest.get("components", {})}
    plan = release_check.facts_plan(root, manifest)
    facts = {**plan, **release_check.gather_version_facts(manifest, str(manifest_path), component_dirs, plan["umbrella"])}
    drifts = release_check.run_checks(facts)
    existing_tags = {name: _remote_tags(spec["repo"]) for name, spec in manifest.get("components", {}).items()
                      if spec.get("repo")}
    pinned_commits = {}
    for name, spec in manifest.get("components", {}).items():
        if spec.get("repo") and not spec.get("lockstep", True):
            directory = release.component_dir(root, name, overrides)
            rc, out = _real_run(["git", "-C", directory, "rev-list", f"{spec['tag']}..HEAD", "--count"], None)
            pinned_commits[name] = int(out.strip()) if rc == 0 and out.strip().isdigit() else 0
    component_versions = {}
    for name, directory in component_dirs.items():
        pyproject_path = Path(directory) / "pyproject.toml"
        if not pyproject_path.exists():
            continue
        found = release.component_version(pyproject_path.read_text())
        if found is not None:
            component_versions[name] = found
    plan_steps = release.release_plan(
        manifest, a.version, existing_tags, component_versions=component_versions, pinned_commits=pinned_commits,
        tools_repository_url=_tools_repository_url(),
        tap_state=_tap_state(release.component_dir(root, release.TAP_CHECKOUT, overrides)))
    steps = release.gate(drifts, a.allow_doc_drift, plan_steps) + plan_steps
    if a.dry_run:
        for step in steps:
            print(f"{step['kind']} {step['component']}: {_release_detail(step)}")
        return 2 if any(step["kind"] == "refuse" for step in steps) else 0
    umbrella = a.umbrella or str(Path(root) / "coxswain")
    return _release_execute(steps, a.version, root, overrides, umbrella, _real_run, manifest, str(manifest_path))


def _backfill_github_releases(a: argparse.Namespace) -> int:
    """Walks the umbrella's docs/releases/<version>.md files oldest-first,
    creating or editing the GitHub Release for every tagged repo at that
    version whose body doesn't already match the one this command would
    write, so a rerun touches nothing already current. Each version's own
    manifest.toml — `git show v<version>:manifest.toml`, falling back to the
    manifest at HEAD when that tag predates the file — decides every
    component's tag; a pinned component not tagged or rejoined this version
    is skipped, since its release was already backfilled at the version it
    was actually cut and a later cut must never rewrite it. `from_tag`
    carries forward the last tag this walk saw for a component, mirroring
    the live step's own pre-bump `spec["tag"]` read rather than the already-
    bumped value that version's own manifest now shows. The first failed
    `gh` write stops the walk, the same as the live `github_release` step."""
    root = a.root
    umbrella = str(Path(root) / "coxswain")
    head_manifest = _load_manifest(Path(umbrella) / "manifest.toml") or {}
    tools_repository_url = _tools_repository_url()
    releases_dir = Path(umbrella) / "docs" / "releases"
    versions = release.versions_oldest_first(p.stem for p in releases_dir.glob("*.md") if p.stem != "index")
    previous_tag: dict[str, str] = {}
    for version in versions:
        show_rc, show_out = _real_run(["git", "-C", umbrella, "show", f"v{version}:manifest.toml"], None)
        manifest = tomllib.loads(show_out) if show_rc == 0 else head_manifest
        slug = release.umbrella_release_slug(manifest, tools_repository_url)
        notes_path = f"docs/releases/{version}.md"
        link = f"https://github.com/{slug}/releases/tag/v{version}" if slug else None
        items = [("coxswain", slug, f"v{version}", umbrella, None, None, f"coxswain {version}")]
        for name, spec in manifest.get("components", {}).items():
            if not spec.get("repo"):
                continue
            tag = f"v{version}" if spec.get("lockstep", True) else spec["tag"]
            from_tag = previous_tag.get(name, spec.get("tag"))
            previous_tag[name] = spec.get("tag")
            if tag == f"v{version}":
                items.append((name, spec["repo"], tag, release.component_dir(root, name),
                              f"## coxswain-{name}", from_tag, f"coxswain-{name} {version}"))
        for _name, repo, tag, directory, heading, from_tag, title in items:
            if not Path(directory).is_dir():
                # An old manifest can name a component that has no checkout
                # here (the 0.2.0 `hud`); its release is not ours to write.
                print(f"skipped {repo} {tag}: no checkout at {directory}")
                continue
            text = (Path(umbrella, notes_path).read_text(encoding="utf-8") if heading is None else
                    _github_release_notes_text(umbrella, notes_path, heading, from_tag, link))
            view_rc, view_out = _real_run(release.github_release_body_argv(tag), directory)
            state = "created" if view_rc != 0 else ("already-current" if view_out.strip() == text.strip() else "edited")
            prefix = "would " if a.dry_run else ""
            shown = {"created": "create", "edited": "edit"}.get(state, state) if a.dry_run else state
            if state != "already-current" and not a.dry_run:
                with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tmp:
                    tmp.write(text)
                argv = (release.github_release_create_argv(tag, title, tmp.name) if state == "created" else
                        release.github_release_edit_argv(tag, tmp.name))
                write_rc, write_out = _real_run(argv, directory)
                if write_rc != 0:
                    print(f"FAILED {repo} {tag}: {write_out.strip()}")
                    return 2
            print(f"{prefix}{shown} {repo} {tag}")
    return 0


def _release_check(a: argparse.Namespace) -> int:
    manifest_path = Path(a.manifest) if a.manifest else Path("coxswain") / "manifest.toml"
    manifest = _load_manifest(manifest_path)
    if manifest is None:
        print(f"refusing: no manifest at {manifest_path}")
        return 2
    root = a.root or "."
    overrides = dict(pair.split("=", 1) for pair in (a.checkout or []))
    component_dirs = {name: release.component_dir(root, name, overrides) for name in manifest.get("components", {})}
    readmes = {name: str(Path(d) / "README.md") for name, d in component_dirs.items()}
    pyprojects = {name: str(Path(d) / "pyproject.toml") for name, d in component_dirs.items()}
    plan = release_check.facts_plan(root, manifest)
    facts = {
        **plan,
        **release_check_cli.gather_cli_facts(root, _real_run),
        **release_check_manifest.gather_manifest_facts(manifest, str(manifest_path), plan["component_docs"], plan["release_notes"]),
        **release_check_notes.gather_notes_facts(root, manifest, subprocess.run),
        **release_check_pages.gather_page_facts(root, manifest, subprocess.run),
        **release_check_readmes.gather_readmes_facts(
            readmes, manifest, release_check_readmes.resolve_docs_base(str(Path(plan["umbrella"]) / "mkdocs.yml"))
        ),
        **release_check.gather_version_facts(manifest, str(manifest_path), component_dirs, plan["umbrella"]),
        **release_check_index.gather_release_index_facts(plan["umbrella"]),
        "pyprojects": pyprojects,
    }
    drifts = release_check.run_checks(facts)
    rendered = release_check.render(drifts, len(release_check.CHECKS))
    print(json.dumps({"checks_run": len(release_check.CHECKS), "drifts": release_check.to_json(drifts)}) if a.json else rendered)
    return 0


def _home(a: argparse.Namespace) -> int:
    from agent_tools import home_screen
    profile, runs_dir, refuse_rc = _leader_runs_dir_or_refuse(a)
    if refuse_rc is not None:
        return refuse_rc
    workspace = runs_dir.parent
    plugin_root = _plugin_root(profile.get("skills_roots") or [])
    return home_screen.main(
        runs_dir, workspace / "work", workspace / "intake", str(plugin_root or ""),
        window_ceiling_usd=profile.get("window_ceiling_usd"),
    )


def _setup_tui(a: argparse.Namespace) -> int:
    if not sys.stdin.isatty():
        print("setup: needs a terminal; use setup doctor / setup install / cartridge init directly")
        return 2
    return setup_screen.main(*_setup_fields(a))


_USAGE_ASSESS_EXIT = {"go": 0, "go_degraded": 0, "hold": 3, "stop": 4}

_GATE_LEVELS = ("ticket", "phase", "epic", "full")


def _resolved_gate_level(runs_dir: Path) -> str:
    """`level` from `<runs_dir>/policy.gate.json`; `ticket` when absent, unreadable or unknown, never `full`."""
    text = _read_text_or_none(runs_dir / "policy.gate.json")
    try:
        raw = json.loads(text) if text is not None else None
    except json.JSONDecodeError:
        raw = None
    level = raw.get("level") if isinstance(raw, dict) else None
    return level if level in _GATE_LEVELS else "ticket"


def _resolved_pacing_policy(runs_dir: Path) -> pacing.Policy:
    """The cartridge's resolved `policy.pacing`, read from
    `<runs_dir>/policy.pacing.json` when a landing step has dropped one there;
    the unmeasured-and-uncapped `usage_window.DEFAULT_POLICY` when the file is
    absent, unreadable, or not a mapping — same skip-not-raise contract as
    `records.ceiling_for`'s own `<run_id>.ceiling.json` handling. A key the
    file omits falls back to the matching `DEFAULT_POLICY` field, not to a
    guess."""
    default = usage_window.DEFAULT_POLICY
    text = _read_text_or_none(runs_dir / "policy.pacing.json")
    if text is None:
        return default
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return default
    if not isinstance(raw, dict):
        return default
    return pacing.Policy(
        pace_thresholds=tuple(raw.get("pace_thresholds", default.pace_thresholds)),
        tier_ladder=tuple(raw.get("tier_ladder", default.tier_ladder)),
        effort_ladder=tuple(raw.get("effort_ladder", default.effort_ladder)),
        min_headroom_usd=float(raw.get("min_headroom_usd", default.min_headroom_usd)),
        hard_stop_fraction=float(raw.get("hard_stop_fraction", default.hard_stop_fraction)),
        weekly_hard_stop_fraction=float(raw.get("weekly_hard_stop_fraction", default.weekly_hard_stop_fraction)),
    )


def _usage_assessment(
    runs_dir, window_ceiling_usd: float | None = None, weekly_ceiling_usd: float | None = None
) -> pacing.Assessment:
    """Computed once via the gatherer and shared by every surface that
    narrates it: `usage assess`, `route context`'s docket line, and `route
    launch`'s gate all call this so the same window yields the same reason.
    """
    now = datetime.datetime.now(datetime.UTC)
    window = usage_window.gather(runs_dir, now, ceiling_usd=window_ceiling_usd)
    weekly = usage_window.gather_weekly(runs_dir, now, weekly_ceiling_usd)
    policy = _resolved_pacing_policy(Path(runs_dir))
    return pacing.assess(window, policy, now, weekly=weekly)


def _usage_assess(a: argparse.Namespace) -> int:
    # Resolved the same tolerant way `_route_context` resolves its profile:
    # a missing or unreadable profile just means no ceiling, not a raise.
    text = _read_text_or_none(_profile_path(a))
    try:
        profile = route.parse_profile(text) if text is not None else {}
    except route.ProfileError:
        profile = {}
    result = _usage_assessment(a.runs_dir, profile.get("window_ceiling_usd"), profile.get("weekly_ceiling_usd"))
    if a.json:
        d = dataclasses.asdict(result)
        d["hold_until"] = result.hold_until.isoformat() if result.hold_until else None
        print(json.dumps(d, indent=2))
    else:
        print(f"{result.verdict}: {result.reason}")
    return _USAGE_ASSESS_EXIT[result.verdict]


def _bare_group(parser: argparse.ArgumentParser):
    """Default `fn` for a group parser whose subcommand is optional: an
    operator who runs the group alone sees that group's own help and a exit
    code of 2, not a traceback or silence."""
    def _fn(_a: argparse.Namespace) -> int:
        parser.print_help()
        return 2
    return _fn


RUNS_GROUP = commands.Group(
    name="runs", help="what a harness run recorded, and cleaning up after it",
    description="What a harness run recorded, and cleaning up after it.",
    epilog="examples:\n  cox runs land <run> --repo PATH --apply\n  cox runs recover <run> <task> --repo PATH\n"
           "  cox runs usage <run> --json\n  cox runs detail <run> --json",
)
RUNS_COMMANDS = [
    commands.Command(
        "usage", "runs", "usage stats and cost for one run",
        (commands.Arg(("run_id",)), commands.Arg(("--runs-dir",), {"default": "runs"}), commands.Arg(("--json",), {"action": "store_true"})),
        _runs_usage, False, (),
    ),
    commands.Command(
        "trace", "runs", "the tool-call trace for one run",
        (commands.Arg(("run_id",)), commands.Arg(("--runs-dir",), {"default": "runs"}), commands.Arg(("--role",)), commands.Arg(("-v", "--verbose"), {"action": "store_true"})),
        _runs_trace, False, (),
    ),
    commands.Command(
        "clean", "runs", "delete a run's worktree and branches locally",
        (
            commands.Arg(("run_id",)), commands.Arg(("--repo",), {"required": True}), commands.Arg(("--worktree-root",), {"default": "~/worktrees"}),
            commands.Arg(("--runs-dir",), {"help": "override: resolve task records here instead of the profile's workspace_dir"}), commands.Arg(("--profile",)),
            commands.Arg(("--apply",), {"action": "store_true"}), commands.Arg(("--force",), {"action": "store_true", "help": "delete every branch regardless of whether its task is on main"}),
        ),
        _runs_clean, False, (),
    ),
    commands.Command(
        "land", "runs", "merge a run's branch into the target repo",
        (
            commands.Arg(("run_id",)), commands.Arg(("--repo",), {"required": True}), commands.Arg(("--task",)),
            commands.Arg(("--phase",), {"help": "land the whole phase off its own epic branch instead of one task"}), commands.Arg(("--label",)),
            commands.Arg(("--force",), {"action": "store_true", "help": "land despite a foreign live leader"}),
            commands.Arg(("--no-claim",), {"action": "store_true", "help": "land without taking an unheld or stale loop"}),
            commands.Arg(("--worktree-root",), {"default": "~/worktrees"}), commands.Arg(("--apply",), {"action": "store_true"}), commands.Arg(("--no-merge",), {"action": "store_true"}),
            commands.Arg(("--runs-dir",), {"help": "override: resolve task records here instead of the profile's workspace_dir"}), commands.Arg(("--profile",)),
            commands.Arg(("--gate",), {"choices": ["ticket", "phase", "epic", "full"], "help": "override the resolved gate level for this invocation"}),
        ),
        _runs_land, False, (),
    ),
    commands.Command(
        "recover", "runs", "merge an approved task's commit into its phase branch after an escalated merge",
        (
            commands.Arg(("run_id",)), commands.Arg(("task_id",)), commands.Arg(("--repo",), {"required": True}),
            commands.Arg(("--runs-dir",), {"help": "override: resolve task records here instead of the profile's workspace_dir"}), commands.Arg(("--profile",)),
            commands.Arg(("--dry-run",), {"action": "store_true", "help": "print the merge that would be made and exit without touching anything"}),
        ),
        _runs_recover, False, (),
    ),
    commands.Command(
        "series", "runs", "per-run summary rows across a runs directory",
        (commands.Arg(("--runs-dir",), {"default": "runs"}), commands.Arg(("--json",), {"action": "store_true"}), commands.Arg(("--append",))),
        _runs_series, False, (),
    ),
    commands.Command(
        "events", "runs", "poll a run's log for structured events",
        (commands.Arg(("--runs-dir",), {"default": "runs"}), commands.Arg(("--follow",), {"action": "store_true"}), commands.Arg(("--json",), {"action": "store_true"})),
        _runs_events, False, (),
    ),
    commands.Command(
        "top", "runs", "live table of runs in flight; --once prints it and exits",
        (commands.Arg(("--runs-dir",), {"default": "runs"}), commands.Arg(("--interval",), {"type": float, "default": 3}), commands.Arg(("--once",), {"action": "store_true"})),
        _runs_top, False, (),
    ),
    commands.Command(
        "bar", "runs", "one Waybar JSON line: runs in flight, cost, class idle|running|attention",
        (commands.Arg(("--runs-dir",), {"default": "runs"}),),
        _runs_bar, False, (),
    ),
    commands.Command(
        "notify", "runs", "desktop notifications for exits, quarantines, budget stops and cost",
        (
            commands.Arg(("--runs-dir",), {"default": "runs"}), commands.Arg(("--once",), {"action": "store_true"}), commands.Arg(("--interval",), {"type": float, "default": 10}),
            commands.Arg(("--replay",), {"action": "store_true", "help": "emit history on first start; default is silent for existing runs when no state file is present"}),
        ),
        _runs_notify, False, (),
    ),
    commands.Command(
        "detail", "runs", "one run's timeline, objection and last tool calls",
        (commands.Arg(("run_id",)), commands.Arg(("--runs-dir",), {"default": "runs"}), commands.Arg(("--json",), {"action": "store_true"})),
        _runs_detail, False, (),
    ),
    commands.Command(
        "stranded", "runs", "every approved task record whose work item is not done, with its remedy",
        (
            commands.Arg(("--runs-dir",), {"help": "override: resolve task records here instead of the profile's workspace_dir"}), commands.Arg(("--profile",)),
            commands.Arg(("--json",), {"action": "store_true"}),
        ),
        _runs_stranded, False, (),
    ),
]

VERSIONS_GROUP = commands.Group(
    name="versions", help="component versions against the manifest", description="", epilog="",
    args=(commands.Arg(("--root",)), commands.Arg(("--manifest",))), fn=_versions,
)

INSTALL_GROUP = commands.Group(
    name="install", help="clone/update coxswain components against the manifest", description="", epilog="",
    args=(
        commands.Arg(("--root",), {"required": True}),
        commands.Arg(("--manifest",)),
        commands.Arg(("--provider",), {"default": "claude-code"}),
        commands.Arg(("--with",), {"action": "append", "default": None, "dest": "with_", "metavar": "FLAG"}),
        commands.Arg(("--team",)),
        commands.Arg(("--workspace",)),
        commands.Arg(("--edge",), {"action": "store_true"}),
        commands.Arg(("--dry-run",), {"action": "store_true"}),
    ),
    fn=_install,
)

UPGRADE_GROUP = commands.Group(
    name="upgrade", help="fetch and check out newer pinned versions; refuses dirty checkouts", description="", epilog="",
    args=(
        commands.Arg(("--root",), {"required": True}),
        commands.Arg(("--manifest",)),
        commands.Arg(("--provider",), {"default": "claude-code"}),
        commands.Arg(("--with",), {"action": "append", "default": None, "dest": "with_", "metavar": "FLAG"}),
        commands.Arg(("--team",)),
        commands.Arg(("--workspace",)),
        commands.Arg(("--to",)),
        commands.Arg(("--dry-run",), {"action": "store_true"}),
    ),
    fn=_upgrade,
)

HOME_GROUP = commands.Group(
    name="home", help="the live dashboard: runs, leader, backlog", description="", epilog="",
    args=(commands.Arg(("--profile",)),), fn=_home,
)

USAGE_GROUP = commands.Group(
    name="usage", help="spend pacing against the ceiling for the current window",
    description="Spend pacing against the ceiling for the current window.",
    epilog="examples:\n  cox usage assess\n  cox usage assess --json --runs-dir runs",
)
USAGE_COMMANDS = [
    commands.Command(
        "assess", "usage", "the pacing verdict for the current spend window",
        (
            commands.Arg(("--json",), {"action": "store_true"}),
            commands.Arg(("--runs-dir",), {"default": "runs"}),
            commands.Arg(("--profile",), {"help": "the routing profile naming the window ceiling (default: ~/.config/agent-tools/profile.yaml or $AGENT_TOOLS_PROFILE)"}),
        ),
        _usage_assess, False, (),
    ),
]

STATS_GROUP = commands.Group(
    name="stats", help="load the run corpus into the stats store",
    description="Load the run corpus into the stats store.",
    epilog="examples:\n  cox stats ingest\n  cox stats ingest runs --db workspace/stats/stats.db"
           "\n  cox stats roles --json\n  cox stats explain build --json\n  cox stats series --json"
           "\n  cox stats coverage --json\n  cox stats bounds --json\n  cox stats spend-mix --json",
)
STATS_COMMANDS = [
    commands.Command(
        "ingest", "stats", "load usage, task, node and launch records into stats.db",
        (
            commands.Arg(("runs_dir",), {"nargs": "?", "default": "runs"}),
            commands.Arg(("--db",), {"default": "workspace/stats/stats.db"}),
            commands.Arg(("--work-store-root",), {"default": "work"}),
            commands.Arg(("--cartridges-repo",), {"default": None}),
        ),
        _stats_ingest, False, (),
    ),
    commands.Command(
        "roles", "stats", "landed rate, attempts-to-land and $/landed per role and model",
        (
            commands.Arg(("--db",), {"default": "workspace/stats/stats.db"}),
            commands.Arg(("--json",), {"action": "store_true"}),
            commands.Arg(("--cartridge-sha",), {"default": None, "help": "keep only runs on this cartridge_sha"}),
            commands.Arg(("--provider-profile",), {"default": None, "help": "keep only runs on this provider_profile"}),
        ),
        _stats_roles, False, (),
    ),
    commands.Command(
        "explain", "stats", "the failure-class breakdown behind one role",
        (
            commands.Arg(("role",)),
            commands.Arg(("--db",), {"default": "workspace/stats/stats.db"}),
            commands.Arg(("--json",), {"action": "store_true"}),
        ),
        _stats_explain, False, (),
    ),
    commands.Command(
        "series", "stats", "per-run summary rows read from the stats store",
        (
            commands.Arg(("--db",), {"default": "workspace/stats/stats.db"}),
            commands.Arg(("--json",), {"action": "store_true"}),
            commands.Arg(("--cartridge-sha",), {"default": None, "help": "keep only runs on this cartridge_sha"}),
            commands.Arg(("--provider-profile",), {"default": None, "help": "keep only runs on this provider_profile"}),
        ),
        _stats_series, False, (),
    ),
    commands.Command(
        "coverage", "stats", "known/total provenance rows for runs, calls and tasks",
        (
            commands.Arg(("--db",), {"default": "workspace/stats/stats.db"}),
            commands.Arg(("--json",), {"action": "store_true"}),
        ),
        _stats_coverage, False, (),
    ),
    commands.Command(
        "bounds", "stats", "n/p50/p95/max and strict/moderate/liberal candidate ceilings per role and model",
        (
            commands.Arg(("--db",), {"default": "workspace/stats/stats.db"}),
            commands.Arg(("--json",), {"action": "store_true"}),
            commands.Arg(("--level",), {"choices": ("strict", "moderate", "liberal"), "default": None}),
            commands.Arg(("--profile",), {"help": "the routing profile naming the provider profile (default: ~/.config/agent-tools/profile.yaml or $AGENT_TOOLS_PROFILE)"}),
            commands.Arg(("--write",), {"default": None, "help": "also write the JSON table to PATH inside the repo checkout"}),
        ),
        _stats_bounds, False, (),
    ),
    commands.Command(
        "spend-mix", "stats", "per-model token counts and cost share by class, plus the build-only split",
        (
            commands.Arg(("--db",), {"default": "workspace/stats/stats.db"}),
            commands.Arg(("--json",), {"action": "store_true"}),
        ),
        _stats_spend_mix, False, (),
    ),
]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cox",
        description=__doc__,
        epilog=(
            "examples:\n"
            "  bare cox opens the coxswain session\n"
            "  cox setup doctor checks this machine\n"
            "  cox route launch epic runs a filed initiative"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # dest="launcher_profile", not "profile": eight subparsers below declare
    # their own `--profile` with default None, and argparse copies a matched
    # subparser's namespace back over the parent's, so a shared dest would
    # let `cox --profile P route status` silently lose P to that default.
    p.add_argument("--profile", dest="launcher_profile", help="bare cox: the profile to launch claude against")
    p.add_argument("--no-plugin", action="store_true", help="bare cox: start claude without --plugin-dir")
    p.add_argument("--print-argv", action="store_true", help="bare cox: print the claude argv and cwd instead of exec'ing it")
    sub = p.add_subparsers(dest="group", required=False)

    group, rows = _table_entry("runs")
    commands.build_parser(rows, [group], sub)

    group, rows = _table_entry("stats")
    commands.build_parser(rows, [group], sub)

    group, rows = _table_entry("usage")
    commands.build_parser(rows, [group], sub)

    group, rows = _table_entry("router")
    commands.build_parser(rows, [group], sub)

    group, rows = _table_entry("steward")
    commands.build_parser(rows, [group], sub)

    group, rows = _table_entry("epic")
    commands.build_parser(rows, [group], sub)

    group, rows = _table_entry("plan")
    commands.build_parser(rows, [group], sub)

    group, rows = _table_entry("route")
    route_p = commands.build_parser(rows, [group], sub)["route"]
    r = _leaf_subparsers(route_p)
    ld = r.add_parser("chair", help="the chair lock for the landing loop (runs/chair.json)")
    ld.set_defaults(fn=_bare_group(ld))
    lds = ld.add_subparsers(dest="chair_cmd", required=False)
    lt = lds.add_parser("take", help="take the chair lock if no live chair holds it")
    lt.add_argument("--profile"); lt.add_argument("--label"); lt.add_argument("--pid", type=int, help="the durable pid that owns the loop (default: the parent process)"); lt.add_argument("--steal", action="store_true"); lt.set_defaults(fn=_route_chair_take)
    lb = lds.add_parser("beat", help="refresh the chair lock's heartbeat")
    lb.add_argument("--profile"); lb.add_argument("--label"); lb.add_argument("--pid", type=int, help="the durable pid that owns the loop (default: the parent process)"); lb.add_argument("--run"); lb.set_defaults(fn=_route_chair_beat)
    lr = lds.add_parser("release", help="release the chair lock this session holds")
    lr.add_argument("--profile"); lr.add_argument("--label"); lr.add_argument("--pid", type=int, help="the durable pid that owns the loop (default: the parent process)"); lr.set_defaults(fn=_route_chair_release)
    lst = lds.add_parser("status", help="the chair lock's holder and computed state")
    lst.add_argument("--profile"); lst.add_argument("--json", action="store_true"); lst.set_defaults(fn=_route_chair_status)
    lcl = lds.add_parser("clear", help="remove the chair lock file, refusing a live holder unless --force")
    lcl.add_argument("--profile"); lcl.add_argument("--force", action="store_true", help="clear the lock even if its recorded pid is live"); lcl.set_defaults(fn=_route_chair_clear)
    lch = lds.add_parser("chat", help="append to or read the leader chat thread (runs/leader.chat.jsonl)")
    lch.add_argument("text", nargs="?"); lch.add_argument("--profile"); lch.add_argument("--read", action="store_true")
    lch.add_argument("--since"); lch.add_argument("--json", action="store_true")
    lch.add_argument("--as-leader", action="store_true", help="send as the lock's holder; refuses unless this process is the live holder")
    lch.set_defaults(fn=_route_chair_chat)
    sy = r.add_parser("sync", help="mirror the work store onto the GitHub Projects board")
    sy.add_argument("--profile"); sy.add_argument("--item"); sy.add_argument("--project"); sy.add_argument("--workspace"); sy.add_argument("--dry-run", action="store_true")
    sy.set_defaults(fn=_route_sync)
    lc = r.add_parser("launch", help="run one of the harness's graphs directly").add_subparsers(dest="graph", required=True)
    ep = lc.add_parser("epic", help="launch the epic graph against a filed initiative"); ep.add_argument("--profile"); ep.add_argument("--initiative", required=True); ep.add_argument("--repo")
    ep.add_argument("--fix-attempts", type=int, default=None); ep.add_argument("--dry-run", action="store_true"); ep.set_defaults(fn=_route_launch, graph="epic")
    de = lc.add_parser("decompose", help="launch the decompose graph against an idea"); de.add_argument("--profile"); de.add_argument("--idea", required=True); de.add_argument("--initiative-id", required=True)
    de.add_argument("--dry-run", action="store_true"); de.set_defaults(fn=_route_launch, graph="decompose")
    co = lc.add_parser("cos", help="launch the cos graph"); co.add_argument("--profile"); co.add_argument("--dry-run", action="store_true")
    co.set_defaults(fn=_route_launch, graph="cos")
    sw = lc.add_parser("sweep", help="launch the sweep graph against an idea"); sw.add_argument("--idea", required=True); sw.add_argument("--initiative-id", required=True)
    sw.add_argument("--label"); sw.add_argument("--dry-run", action="store_true"); sw.set_defaults(fn=_route_launch_sweep)
    ep.add_argument("--include-blocked", action="store_true", help="launch despite a ready task behind a blocked item (--force does not)")
    for _launch_parser in (ep, de, co):
        _launch_parser.add_argument("--tier-ceiling", choices=("cheap", "standard", "deep"))
        _launch_parser.add_argument("--effort-ceiling", choices=("low", "high"))
        _launch_parser.add_argument("--force", action="store_true", help="launch despite a usage stop or a foreign live leader")
        _launch_parser.add_argument("--no-claim", action="store_true", help="launch without taking an unheld or stale loop")
        _launch_parser.add_argument("--label")

    group, rows = _table_entry("courier")
    commands.build_parser(rows, [group], sub)

    group, rows = _table_entry("install")
    commands.build_parser(rows, [group], sub)

    group, rows = _table_entry("upgrade")
    commands.build_parser(rows, [group], sub)

    group, rows = _table_entry("versions")
    commands.build_parser(rows, [group], sub)

    group, rows = _table_entry("dev")
    commands.build_parser(rows, [group], sub)

    old_rel = sub.add_parser("release", help=argparse.SUPPRESS)
    # swallow every flag the old form took, so the hint prints instead of argparse erroring
    old_rel.add_argument("rest", nargs=argparse.REMAINDER)
    old_rel.set_defaults(fn=_release_moved)

    group, rows = _table_entry("home")
    commands.build_parser(rows, [group], sub)

    group, rows = _table_entry("setup")
    setup_p = commands.build_parser(rows, [group], sub)["setup"]
    # commands.build_parser's generic bare-group fallback prints help and
    # exits 2; a bare `cox setup` instead opens the TUI, so override it here.
    setup_p.set_defaults(fn=_setup_tui)
    return p


def _plugin_root(skills_roots, name: str = "coxswain"):
    """First skills root carrying `<root>/<name>/.claude-plugin/plugin.json`,
    or None if none of them do."""
    for root in skills_roots:
        candidate = Path(root).expanduser() / name
        if (candidate / ".claude-plugin" / "plugin.json").exists():
            return candidate
    return None


def _launcher_argv(plugin_root, skills_roots, no_plugin: bool, extra_args: list[str]):
    """spec §7: the argv for a bare `cox` — real Claude Code with the
    coxswain plugin loaded, unless `no_plugin` or no skills root carries one.
    Takes the already-resolved `plugin_root` (a Path, or None if none of
    `skills_roots` carries one) as data: no filesystem probe here, so this is
    testable with literals. `extra_args` are appended as given — the caller's
    own `--` already did the job of separating them from cox's own flags, so
    none is re-inserted here. Returns (argv, warning); warning is the
    one-line fallback notice, or None when a plugin was found or none was
    asked for."""
    plugin_flag = [] if no_plugin or plugin_root is None else ["--plugin-dir", str(plugin_root)]
    warning = None if no_plugin or plugin_root is not None else f"coxswain plugin not found under {skills_roots}; starting plain claude"
    return ["claude", *plugin_flag, *extra_args], warning


def _spawn(argv: list[str]) -> subprocess.Popen:
    """Detached: own session via `start_new_session`, stdio to devnull, so `argv` outlives the caller's later `execvp`."""
    return subprocess.Popen(
        argv, start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _courier_workspace(a: argparse.Namespace) -> Path | None:
    profile, reason, *_ = _gather_context(_profile_path(a))
    if reason: print(f"routing: {reason}"); return None
    return Path(profile["workspace_dir"]).expanduser()


def _courier_send(a: argparse.Namespace) -> int:
    workspace = _courier_workspace(a)
    if workspace is None: return 2
    ref = courier.parse_reference(a.ref)
    if ref is None or courier.resolve(ref, workspace) is None:
        print(f"courier: {a.ref} does not resolve"); return 2
    path = workspace / "courier.jsonl"
    sender = (chair.read(workspace / "runs") or {}).get("session") or "cli"
    entry = courier.send(ref, sender, a.to, a.note, uuid.uuid4().hex)
    path.write_text(courier.append_line(_read_text_or_none(path) or "", entry), encoding="utf-8")
    return 0


def _print_inbox(blob: str, label: str | None, holder: str | None) -> None:
    for entry in courier.inbox(blob, label, holder=holder):
        print(f"{entry['id']}: {entry['from']} -> {entry['to']}: {entry['note']} ({entry['ref']})")


def _lock_holder_or_none(runs_dir: Path) -> str | None:
    """A missing, unreadable, or torn lock file has no holder; it does not refuse
    a read-only listing (cf. `runs_detail_screen.facts_for`'s same degrade)."""
    try:
        return (chair.read(runs_dir) or {}).get("session")
    except (OSError, json.JSONDecodeError):
        return None


def _courier_inbox(a: argparse.Namespace) -> int:
    workspace = _courier_workspace(a)
    if workspace is None: return 2
    holder = _lock_holder_or_none(workspace / "runs")
    _print_inbox(_read_text_or_none(workspace / "courier.jsonl") or "", a.label, holder)
    return 0


def _courier_ack(a: argparse.Namespace) -> int:
    workspace = _courier_workspace(a)
    if workspace is None: return 2
    path = workspace / "courier.jsonl"
    blob = _read_text_or_none(path) or ""
    updated = courier.ack(blob, a.id)
    if updated == blob: print(f"courier: no entry {a.id}"); return 2
    path.write_text(updated, encoding="utf-8")
    return 0


COURIER_GROUP = commands.Group(name="courier", help="the courier bus: hand a reference to another label", description="", epilog="")
COURIER_COMMANDS = [
    commands.Command(
        "send", "courier", "append a bus entry naming a courier reference",
        (commands.Arg(("ref",)), commands.Arg(("--to",), {"required": True}), commands.Arg(("--note",), {"required": True}), commands.Arg(("--profile",))),
        _courier_send, False, (),
    ),
    commands.Command(
        "inbox", "courier", "list this label's unacknowledged bus entries",
        (commands.Arg(("--label",)), commands.Arg(("--profile",))),
        _courier_inbox, False, (),
    ),
    commands.Command(
        "ack", "courier", "acknowledge one bus entry by id",
        (commands.Arg(("id",)), commands.Arg(("--profile",))),
        _courier_ack, False, (),
    ),
]

PLAN_GROUP = commands.Group(
    name="plan", help="visual plans through the local bridge",
    description="Visual plans through the local bridge.",
    epilog="examples:\n  cox plan serve work/<id> --kind plan\n  cox plan serve work/<id> --check",
)
PLAN_COMMANDS = [
    commands.Command(
        "serve", "plan", "serve a visual plan through the local bridge",
        (commands.Arg(("dir",)), commands.Arg(("--kind",), {"default": "plan"}), commands.Arg(("--check",), {"action": "store_true"}), commands.Arg(("--no-open",), {"action": "store_true"})),
        _plan_serve, False, (),
    ),
]

EPIC_GROUP = commands.Group(
    name="epic", help="watch a detached run",
    description="Watch a detached run.",
    epilog="examples:\n  cox epic watch RUN.pid --log RUN.log\n  cox epic watch RUN.pid --json",
)
EPIC_COMMANDS = [
    commands.Command(
        "watch", "epic", "poll a detached run's pidfile until it exits",
        (
            commands.Arg(("pidfile",)), commands.Arg(("--log",)),
            commands.Arg(("--max-seconds",), {"type": float, "default": 570}), commands.Arg(("--interval",), {"type": float, "default": 20}),
            commands.Arg(("--json",), {"action": "store_true"}),
        ),
        _epic_watch, False, (),
    ),
]

ROUTE_GROUP = commands.Group(
    name="route", help="file work for the harness, and see what is queued or running",
    description="File work for the harness, and see what is queued or running.",
    epilog="examples:\n  cox route launch epic --initiative work/<id> --repo PATH\n  cox route status --profile PATH",
)
ROUTE_COMMANDS = [
    commands.Command(
        "context", "route", "the routing profile's resolved context",
        (commands.Arg(("--profile",)), commands.Arg(("--json",), {"action": "store_true"})),
        _route_context, False, (),
    ),
    commands.Command(
        "status", "route", "what is queued or running for this profile",
        (commands.Arg(("--profile",)), commands.Arg(("--json",), {"action": "store_true"})),
        _route_status, False, (),
    ),
    commands.Command(
        "file", "route", "file a new ticket for the harness",
        (
            commands.Arg(("--profile",)), commands.Arg(("--repo",)), commands.Arg(("--title",)), commands.Arg(("--body",)),
            commands.Arg(("--phase",), {"default": "build"}), commands.Arg(("--intake",), {"action": "store_true"}),
            commands.Arg(("--from-intake",), {"help": "link and file an existing intake file's initiative, then retire it"}),
        ),
        _route_file, False, (),
    ),
    commands.Command(
        "lint", "route", "static ticket lint over a filed initiative, work-shape.md §3",
        (commands.Arg(("initiative_dir",)), commands.Arg(("--repo",), {"default": None})),
        _route_lint, False, (),
    ),
    commands.Command(
        "groups", "route", "print the newest plans/intake-groups/<date>.md file, work-shape.md §5",
        (commands.Arg(("--profile",)),),
        _route_groups, False, (),
    ),
]

def _setup_doctor_row(a: argparse.Namespace) -> int:
    """Indirect through the module global, not a frozen reference: SETUP_COMMANDS
    is built once at import, but test_setup_screen.py monkeypatches
    `cli._setup_doctor` per test and expects the swap honoured."""
    return _setup_doctor(a)


SETUP_GROUP = commands.Group(
    name="setup", help="does this machine's profile actually work",
    description="Does this machine's profile actually work.",
    epilog="examples:\n  cox setup doctor --profile PATH\n  cox setup install --root PATH --team NAME --workspace PATH",
    args=(commands.Arg(("--profile",)),),
)
SETUP_COMMANDS = [
    commands.Command(
        "doctor", "setup", "check this machine's profile against what it needs",
        (
            commands.Arg(("--profile",)),
            commands.Arg(("--repo",), {"help": "target repo to check for a .agent/cartridge.yaml overlay (default: cwd)"}),
            commands.Arg(("--json",), {"action": "store_true"}),
        ),
        _setup_doctor_row, False, (),
    ),
    commands.Command(
        "install", "setup", "clone components and write a profile for this machine",
        (
            commands.Arg(("--root",), {"required": True}), commands.Arg(("--team",), {"required": True}), commands.Arg(("--workspace",), {"required": True}),
            commands.Arg(("--provider-profile",)), commands.Arg(("--skills-root",)), commands.Arg(("--assume",), {"default": "a", "choices": ("a", "r")}),
            commands.Arg(("--plugins",), {"action": "store_true"}), commands.Arg(("--hook",), {"action": "store_true"}),
            commands.Arg(("--force-profile",), {"action": "store_true"}), commands.Arg(("--dry-run",), {"action": "store_true"}),
            commands.Arg(("--window-ceiling-usd",), {"type": float, "default": None, "dest": "window_ceiling_usd", "help": "write spend: window_ceiling_usd into the profile"}),
            commands.Arg(("--weekly-ceiling-usd",), {"type": float, "default": None, "dest": "weekly_ceiling_usd", "help": "write spend: weekly_ceiling_usd into the profile"}),
        ),
        _setup_install, False, (),
    ),
]


def _dev_commands(a: argparse.Namespace) -> int:
    """`cox dev commands render`: rewrite the README's marked Commands block,
    and with --pages-dir the slash-command pages, from `COMMAND_TABLE`."""
    if a.target in ("all", "readme"):
        readme = Path(a.readme)
        text = _read_text_or_none(readme)
        if text is None:
            print(f"commands render: cannot read {readme}"); return 2
        updated = commands_render.splice(text, commands_render.render_readme_block(COMMAND_TABLE))
        if updated is None:
            print(f"commands render: {readme} lacks the {commands_render.BEGIN} / {commands_render.END} markers"); return 2
        readme.write_text(updated, encoding="utf-8")
    if a.target == "pages" and not a.pages_dir:
        print("commands render: --target pages needs --pages-dir"); return 2
    if a.target in ("all", "pages") and a.pages_dir:
        out = Path(a.pages_dir)
        out.mkdir(parents=True, exist_ok=True)
        for name, page in commands_render.render_pages(COMMAND_TABLE).items():
            (out / name).write_text(page, encoding="utf-8")
    return 0


DEV_GROUP = commands.Group(
    name="dev", help="maintainer commands for the Coxswain repositories",
    description="maintainer commands for the Coxswain repositories; not needed to use Coxswain",
    epilog="",
)
DEV_COMMANDS = [
    commands.Command(
        "release", "dev", "the lockstep tag/bump-manifest/notes plan across coxswain's manifest, or (without --dry-run) tags and pushes every component",
        (
            commands.Arg(("version",)),
            commands.Arg(("--manifest",)),
            commands.Arg(("--dry-run",), {"action": "store_true"}),
            commands.Arg(("--root",), {"default": "."}),
            commands.Arg(("--checkout",), {"action": "append", "default": None, "metavar": "NAME=PATH"}),
            commands.Arg(("--umbrella",)),
            commands.Arg(("--allow-doc-drift",), {"dest": "allow_doc_drift", "metavar": "REASON", "default": None,
                                                    "help": "proceed despite a standing release-check drift, naming why"}),
        ),
        _release, False, (),
    ),
    commands.Command(
        "release-check", "dev", "gather facts and print drifts between the CLI, the manifest, the docs and the release notes",
        (
            commands.Arg(("--manifest",)),
            commands.Arg(("--root",), {"default": "."}),
            commands.Arg(("--json",), {"action": "store_true"}),
            commands.Arg(("--checkout",), {"action": "append", "default": None, "metavar": "NAME=PATH"}),
        ),
        _release_check, False, (),
    ),
    commands.Command(
        "backfill-github-releases", "dev",
        "walk the umbrella's past docs/releases/<version>.md files oldest-first, creating or editing the GitHub Release for every tagged repo missing or drifted from one",
        (
            commands.Arg(("--root",), {"required": True, "help": "the checkouts root containing the umbrella and every component"}),
            commands.Arg(("--dry-run",), {"action": "store_true"}),
        ),
        _backfill_github_releases, False, (),
    ),
    commands.Command(
        "commands", "dev", "write the plugin's slash-command pages and the README's Commands section from the command table",
        (
            commands.Arg(("verb",), {"choices": ("render",), "help": "the only action: render"}),
            commands.Arg(("--target",), {"choices": ("all", "pages", "readme"), "default": "all"}),
            commands.Arg(("--pages-dir",), {"help": "where the slash-command pages are written; without it only the README is rendered"}),
            commands.Arg(("--readme",), {"default": "README.md"}),
        ),
        _dev_commands, False, (),
    ),
]

ROUTER_GROUP = commands.Group(
    name="router", help="the routing-profile flag and the tier decision it gates",
    description="The routing-profile flag and the tier decision it gates.",
    epilog="examples:\n  cox router select --role build\n  cox router select --role build --json",
)
ROUTER_COMMANDS = [
    commands.Command(
        "select", "router", "the effective tier for a role under the profile's router: off|shadow|on flag",
        (
            commands.Arg(("--role",), {"required": True}),
            commands.Arg(("--db",), {"default": "workspace/stats/stats.db"}),
            commands.Arg(("--profile",), {"help": "the routing profile naming the provider profile and the router flag (default: ~/.config/agent-tools/profile.yaml or $AGENT_TOOLS_PROFILE)"}),
            commands.Arg(("--json",), {"action": "store_true"}),
        ),
        _router_select, False, (),
    ),
]

STEWARD_GROUP = commands.Group(
    name="steward", help="ceiling-change candidates from stats.db, proposed as intake files",
    description="Ceiling-change candidates from stats.db, proposed as intake files.",
    epilog="examples:\n  cox steward propose\n  cox steward propose --json",
)
STEWARD_COMMANDS = [
    commands.Command(
        "propose", "steward", "write each candidate clearing the evidence bar as a new intake file; never edits a provider profile",
        (
            commands.Arg(("--db",), {"default": "workspace/stats/stats.db"}),
            commands.Arg(("--profile",), {"help": "the routing profile naming the provider profile (default: ~/.config/agent-tools/profile.yaml or $AGENT_TOOLS_PROFILE)"}),
            commands.Arg(("--json",), {"action": "store_true"}),
        ),
        _steward_propose, False, (),
    ),
]

COMMAND_TABLE: list[tuple[commands.Group, list[commands.Command]]] = [
    (RUNS_GROUP, RUNS_COMMANDS),
    (COURIER_GROUP, COURIER_COMMANDS),
    (VERSIONS_GROUP, []),
    (INSTALL_GROUP, []),
    (UPGRADE_GROUP, []),
    (HOME_GROUP, []),
    (STATS_GROUP, STATS_COMMANDS),
    (USAGE_GROUP, USAGE_COMMANDS),
    (PLAN_GROUP, PLAN_COMMANDS),
    (EPIC_GROUP, EPIC_COMMANDS),
    (ROUTE_GROUP, ROUTE_COMMANDS),
    (ROUTER_GROUP, ROUTER_COMMANDS),
    (STEWARD_GROUP, STEWARD_COMMANDS),
    (SETUP_GROUP, SETUP_COMMANDS),
    (DEV_GROUP, DEV_COMMANDS),
]


def _leaf_subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    """The `dest="cmd"` subparsers action `commands.build_parser` already
    attached to `parser`, so a group's own further-nested subcommands
    (route's `chair`, `launch`) attach to that one action instead of a
    second `add_subparsers` call argparse would refuse."""
    return next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))


def _table_entry(name: str) -> tuple[commands.Group, list[commands.Command]]:
    """`COMMAND_TABLE`'s (Group, rows) pair for `name`, so `build_parser` can
    register each migrated group at its own original position -- interleaved
    among the groups still hand-built -- instead of moving every migrated
    group's registration to wherever the table is folded, which would reorder
    `cox --help`'s top-level listing."""
    return next((group, rows) for group, rows in COMMAND_TABLE if group.name == name)


def _launcher(a: argparse.Namespace, extra_args: list[str]) -> int:
    """Bare `cox` (spec §7): a real Claude Code session with the coxswain
    plugin loaded and the profile's workspace as cwd. Resolves the profile
    the same way `setup doctor` does, via `_profile_path`, off the dedicated
    `launcher_profile` dest. A missing profile is the usual help, exit 2 —
    this launcher needs a real workspace to run in, not a one-liner; an
    unreadable one or a missing `workspace_dir` gets the same one-line reason
    `route`'s own commands print, not swallowed into that help text."""
    profile_path = _profile_path(argparse.Namespace(profile=a.launcher_profile))
    text = _read_text_or_none(profile_path)
    if text is None:
        build_parser().print_help()
        return 2
    try:
        profile = route.parse_profile(text)
    except route.ProfileError as exc:
        print(f"routing: profile unreadable: {exc}")
        return 2
    workspace = profile.get("workspace_dir", "")
    if not workspace:
        print(f"routing: workspace_dir not set in profile {profile_path}")
        return 2
    if shutil.which("claude") is None:
        print("claude not found on PATH")
        return 2
    skills_roots = profile.get("skills_roots") or []
    plugin_root = None if a.no_plugin else _plugin_root(skills_roots)
    argv, warning = _launcher_argv(plugin_root, skills_roots, a.no_plugin, extra_args)
    if warning:
        print(warning)
    cwd = str(Path(workspace).expanduser())
    if a.print_argv:
        print(argv)
        print(cwd)
        return 0
    # steal=True: chair.take only lets `steal` override a stale/crashed record
    # (agent_tools/chair.py `take`), never a live one, so a prior bare-`cox`
    # session's own now-unbeaten lock is retaken rather than refused on every
    # later invocation, while a LIVE foreign holder is still only printed.
    chair_a = argparse.Namespace(
        profile=a.launcher_profile,
        label=f"chair-{datetime.datetime.now(datetime.UTC):%Y-%m-%d}",
        pid=os.getpid(),
        steal=True,
    )
    if _route_chair_take(chair_a) == 0:
        runs_dir = Path(workspace).expanduser() / "runs"
        beater_argv = [
            sys.executable, "-m", "agent_tools.chair", "beat-loop",
            "--label", chair_a.label, "--pid", str(chair_a.pid), "--runs-dir", str(runs_dir),
        ]
        try:
            beater = _spawn(beater_argv)
        except OSError as exc:
            print(f"chair: beater failed to start: {exc}")
        else:
            print(f"chair: beating from pid {beater.pid}")
        # chair_a.label is the label `_route_chair_take` just wrote as the holder above;
        # it is the holder, not a re-read of the file this same call just produced.
        _print_inbox(_read_text_or_none(Path(workspace).expanduser() / "courier.jsonl") or "", chair_a.label, chair_a.label)
    os.chdir(cwd)
    os.execvp(argv[0], argv)
    return 0


_TOP_OPTIONS_WITH_VALUE = ("--profile",)
_TOP_FLAGS = ("--no-plugin", "--print-argv")


def _bare_launcher_split(args: list[str]):
    """Splits a bare-`cox` invocation's `-- EXTRA...` tail off before the real
    parser sees it: the top-level subparsers action is a PARSER-style
    positional and would otherwise try to consume the first extra token as an
    (invalid) subcommand name. Returns (head, tail) only when `args`, up to
    any `--`, holds nothing but the launcher's own top-level options; returns
    None the moment a subcommand token (or anything else) appears, so `main`
    leaves that invocation, `--` and all, to the ordinary parser untouched —
    an existing subcommand's own `--` semantics (e.g. `route file -- -title`)
    are not this function's to change."""
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--":
            return args[:i], args[i + 1 :]
        if tok in _TOP_OPTIONS_WITH_VALUE:
            i += 1
            if i >= len(args):
                return None
        elif tok not in _TOP_FLAGS:
            return None
        i += 1
    return args, []


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if not args and not sys.stdout.isatty():
        return _route_status(argparse.Namespace(profile=None, json=False))
    split = _bare_launcher_split(args)
    head, tail = split if split is not None else (args, [])
    a = build_parser().parse_args(head)
    if not hasattr(a, "fn"):
        return _launcher(a, tail)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
