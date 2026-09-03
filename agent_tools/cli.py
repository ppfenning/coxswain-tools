"""`agent-tools`: the seats' deterministic tools, one subcommand per thing a seat would otherwise reason about."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_tools import cleanup, epic, hud, plan, records


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
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
