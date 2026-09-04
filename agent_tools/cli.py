"""`agent-tools`: the seats' deterministic tools, one subcommand per thing a seat would otherwise reason about."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

from agent_tools import cleanup, epic, hud, plan, records, route


def _runs_usage(a: argparse.Namespace) -> int:
    path = Path(a.runs_dir) / f"{a.run_id}.usage.json"
    s = records.usage_summary(records.load_usage(path))
    if a.json:
        print(json.dumps(s, indent=2)); return 0
    print(f"{s['run_id']}: {s['calls']} calls, {s['turns']} turns, ${s['cost_usd']:.2f}, cache-read share {s['cache_read_share']}")
    print(records.format_table([{"role": k, **v} for k, v in s["by_role"].items()], ["role", "calls", "cost_usd", "turns"]))
    print(); print(records.format_table([{"model": k, **v} for k, v in s["by_model"].items()], ["model", "calls", "cost_usd", "turns"]))
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
    p = cleanup.plan_cleanup(run_id=a.run_id, worktrees=cleanup.git_worktrees(repo), branches=cleanup.git_branches(repo), worktree_root=a.worktree_root)
    for line in cleanup.apply_cleanup(repo, p, dry_run=not a.apply):
        print(line)
    if not a.apply:
        print("(dry run — pass --apply to do it)")
    return 0


def _epic_watch(a: argparse.Namespace) -> int:
    out = epic.watch(a.pidfile, log=a.log, max_seconds=a.max_seconds, interval=a.interval)
    print(json.dumps(out, indent=2) if a.json else "\n".join(
        [f"pid {out['pid']}: {'finished' if out['finished'] else 'still running'}",
         *out.get("quarantined", []), *out.get("reused", []), *(x for x in (out.get("summary"), out.get("usage")) if x)]))
    return 0 if out["finished"] else 3


def _hud_ops(a: argparse.Namespace) -> int:
    items = json.load(sys.stdin if a.file == "-" else open(a.file))
    print(json.dumps(hud.post_ops(items if isinstance(items, list) else items.get("items", []), base=a.base)))
    return 0


def _hud_say(a: argparse.Namespace) -> int:
    print(json.dumps(hud.say(a.text, persona=a.persona, voice=a.voice, base=a.base))); return 0


def _hud_inbox(a: argparse.Namespace) -> int:
    if a.action == "show":
        for i in hud.inbox(base=a.base):
            print(f"{i.get('ts')}\t{i.get('text')}")
    elif a.action == "clear":
        print(json.dumps(hud.clear_inbox(base=a.base)))
    else:
        items = hud.wait_for_inbox(base=a.base, max_seconds=a.max_seconds)
        for i in items:
            print(f"{i.get('ts')}\t{i.get('text')}")
        if not items:
            print("(no directive within the cap)")
    return 0


def _hud_cast(a: argparse.Namespace) -> int:
    for s in hud.cast(base=a.base):
        print(f"{s['name']:10} {s.get('voice',''):12} {s.get('lane',''):7} {s.get('surface','')}")
    return 0


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
    return datetime.datetime.fromtimestamp(mtime, tz=datetime.timezone.utc).isoformat()


def _gather_context(profile_path: Path):
    """Read the profile and the workspace; return (profile_or_none, reason, intake, runs, initiatives)."""
    text = _read_text_or_none(profile_path)
    if text is None:
        return None, f"no profile at {profile_path}", [], [], []
    try:
        profile = route.parse_profile(text)
    except route.ProfileError as exc:
        return None, f"profile unreadable: {exc}", [], [], []
    workspace = profile.get("workspace_dir", "")
    if not workspace:
        return profile, "workspace_dir not set in profile", [], [], []
    ws = Path(workspace).expanduser()
    intake_files = {p.name: t for p in sorted((ws / "intake").glob("*.md")) if (t := _read_text_or_none(p)) is not None}
    pid_paths = sorted((ws / "runs").glob("*.pid"))
    pids = {p.stem: t for p in pid_paths if (t := _read_text_or_none(p)) is not None}
    alive = {rid: (pid := route.parse_pid(t)) is not None and epic.alive(pid) for rid, t in pids.items()}
    started = {rid: _mtime_iso(ws / "runs" / f"{rid}.pid") for rid in pids}
    # the initiative id is the DIRECTORY name: work/<initiative>/<phase>/<task>.md;
    # the edge only reads and names the path parts — route.work_item normalises
    items = [
        route.work_item(fields, initiative=task_path.parent.parent.name, phase_dir=task_path.parent.name, stem=task_path.stem)
        for task_path in sorted((ws / "work").glob("*/*/*.md"))
        if task_path.name != "initiative.md"
        for text in [_read_text_or_none(task_path)] if text is not None
        for fields in [route.parse_frontmatter(text)[0]]
    ]
    return (profile, "",
            route.intake_entries(intake_files),
            route.run_entries(pids, alive, started),
            route.initiative_summaries(items))


def _route_context(a: argparse.Namespace) -> int:
    # The edge is allowed to catch everything: this command must never take a
    # session down with it (spec §2). Charter A6 puts exceptions at the edge.
    try:
        profile, reason, intake, runs, initiatives = _gather_context(_profile_path(a))
        if a.json:
            doc = route.context_document(profile, intake, runs, initiatives)
            if reason:
                doc["reason"] = reason
            print(json.dumps(doc, indent=2))
        elif reason:
            # nothing was read, so print no counts: an unread workspace must not
            # look like an empty one
            first_line = route.render_context(profile, [], [], []).partition("\n")[0]
            print(f"{first_line} ({reason})")
        else:
            print(route.render_context(profile, intake, runs, initiatives))
    except Exception as exc:  # noqa: BLE001 — edge guard, see comment above
        print(f"routing: context unavailable ({type(exc).__name__}: {exc})")
    return 0


def _run_alive(pid):
    """`epic.alive`, guarded: pid 0 or negative is never dispatched to
    `os.kill` (pid 0 signals the caller's whole process group, not a run),
    and a pidfile large enough to overflow `os.kill`'s pid_t reads as dead
    rather than crashing the session.
    """
    if pid is None or pid <= 0:
        return False
    try:
        return epic.alive(pid)
    except OverflowError:
        return False


def _status_rows_for(runs_dir: Path) -> list:
    pids = {p.stem: t for p in sorted(runs_dir.glob("*.pid")) if (t := _read_text_or_none(p)) is not None}
    alive = {run_id: _run_alive(route.parse_pid(t)) for run_id, t in pids.items()}
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
        rows = _status_rows_for(Path(workspace).expanduser() / "runs")
        print(json.dumps(rows, indent=2) if a.json else route.render_status(rows))
    except Exception as exc:  # noqa: BLE001 — edge guard, see comment above
        print(f"routing: status unavailable ({type(exc).__name__}: {exc})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agent-tools", description=__doc__)
    sub = p.add_subparsers(dest="group", required=True)

    runs = sub.add_parser("runs", help="what a harness run recorded, and cleaning up after it").add_subparsers(dest="cmd", required=True)
    u = runs.add_parser("usage"); u.add_argument("run_id"); u.add_argument("--runs-dir", default="runs"); u.add_argument("--json", action="store_true"); u.set_defaults(fn=_runs_usage)
    t = runs.add_parser("trace"); t.add_argument("run_id"); t.add_argument("--runs-dir", default="runs"); t.add_argument("--role"); t.add_argument("-v", "--verbose", action="store_true"); t.set_defaults(fn=_runs_trace)
    c = runs.add_parser("clean"); c.add_argument("run_id"); c.add_argument("--repo", required=True); c.add_argument("--worktree-root", default="~/worktrees"); c.add_argument("--apply", action="store_true"); c.set_defaults(fn=_runs_clean)

    e = sub.add_parser("epic", help="watch a detached run").add_subparsers(dest="cmd", required=True)
    w = e.add_parser("watch"); w.add_argument("pidfile"); w.add_argument("--log"); w.add_argument("--max-seconds", type=float, default=570); w.add_argument("--interval", type=float, default=20); w.add_argument("--json", action="store_true"); w.set_defaults(fn=_epic_watch)

    h = sub.add_parser("hud", help="the voice HUD's HTTP contracts").add_subparsers(dest="cmd", required=True)
    for name, fn in (("ops", _hud_ops), ("say", _hud_say), ("inbox", _hud_inbox), ("cast", _hud_cast)):
        sp = h.add_parser(name); sp.add_argument("--base", default=hud.DEFAULT_BASE); sp.set_defaults(fn=fn)
        if name == "ops": sp.add_argument("file", help="JSON list of items, or - for stdin")
        if name == "say": sp.add_argument("text"); sp.add_argument("--persona"); sp.add_argument("--voice")
        if name == "inbox": sp.add_argument("action", choices=["show", "arm", "clear"]); sp.add_argument("--max-seconds", type=float, default=600)

    pl = sub.add_parser("plan", help="visual plans through the local bridge").add_subparsers(dest="cmd", required=True)
    s = pl.add_parser("serve"); s.add_argument("dir"); s.add_argument("--kind", default="plan"); s.add_argument("--check", action="store_true"); s.add_argument("--no-open", action="store_true"); s.set_defaults(fn=_plan_serve)

    r = sub.add_parser("route", help="file work for the harness, and see what is queued or running").add_subparsers(dest="cmd", required=True)
    ctx = r.add_parser("context"); ctx.add_argument("--profile"); ctx.add_argument("--json", action="store_true"); ctx.set_defaults(fn=_route_context)
    st = r.add_parser("status"); st.add_argument("--profile"); st.add_argument("--json", action="store_true"); st.set_defaults(fn=_route_status)
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
