"""`agent-tools`: the seats' deterministic tools, one subcommand per thing a seat would otherwise reason about."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
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


def _route_file(a: argparse.Namespace) -> int:
    profile, rc = _resolve_profile_or_refuse(a)
    if rc is not None:
        return rc
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
            date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
            mapping = route.intake_file(a.title, body, a.repo, date)
        else:
            mapping = route.initiative_files(a.title, body, a.repo, phase=a.phase)
    except ValueError as exc:
        print(f"routing: {exc}")
        return 2
    ws = Path(profile["workspace_dir"]).expanduser()
    targets = {rel: ws / rel for rel in mapping}
    existing = [str(path) for path in targets.values() if path.exists()]
    if existing:
        print(f"routing: refusing to overwrite existing path(s): {', '.join(existing)}")
        return 2
    for rel, path in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(mapping[rel], encoding="utf-8")
        print(str(path))
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


def _route_launch(a: argparse.Namespace) -> int:
    profile, rc = _resolve_profile_or_refuse(a)
    if rc is not None:
        return rc
    harness_dir = profile.get("harness_dir", "")
    venv_rc = _harness_ready_or_refuse(harness_dir)
    if venv_rc is not None:
        return venv_rc
    runs_dir = Path(profile["workspace_dir"]).expanduser() / "runs"
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
        live_pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)\.pid$")
        for pidfile in sorted(runs_dir.glob(f"{prefix}-*.pid")):
            if not live_pattern.fullmatch(pidfile.name):
                continue
            pid_text = _read_text_or_none(pidfile)
            pid = route.parse_pid(pid_text) if pid_text is not None else None
            if _run_alive(pid):
                print(f"routing: {pidfile.stem} for initiative {prefix} is already running (pid {pid})")
                return 2
        run_id = route.next_run_id([p.name for p in runs_dir.iterdir()], prefix)
        needs = {"initiative": a.initiative, "repo": repo}
        if a.fix_attempts is not None:
            needs["fix_attempts"] = a.fix_attempts
        env_repo = repo
    else:  # decompose
        idea_path = Path(a.idea)
        if not idea_path.exists():
            print(f"routing: no idea file at {idea_path}")
            return 2
        run_id = route.next_run_id([p.name for p in runs_dir.iterdir()], a.initiative_id)
        needs = {"idea": a.idea, "initiative_id": a.initiative_id}
        env_repo = ""

    argv = route.harness_argv(profile, a.graph, run_id, **needs)
    env = route.child_env(dict(os.environ), harness_dir=harness_dir, repo=env_repo)
    log_path = runs_dir / f"{run_id}.log"
    pid_path = runs_dir / f"{run_id}.pid"

    if a.dry_run:
        print(f"dry-run: {' '.join(argv)}")
        print(f"pid {pid_path}")
        print(f"log {log_path}")
        return 0

    with open(log_path, "ab") as log:
        proc = subprocess.Popen(
            argv, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
        )
    pid_path.write_text(str(proc.pid))
    print(f"run {run_id}")
    print(f"pid {pid_path}")
    print(f"log {log_path}")
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
    f = r.add_parser("file"); f.add_argument("--profile"); f.add_argument("--repo", required=True); f.add_argument("--title", required=True)
    f.add_argument("--body"); f.add_argument("--phase", default="build"); f.add_argument("--intake", action="store_true"); f.set_defaults(fn=_route_file)
    lc = r.add_parser("launch").add_subparsers(dest="graph", required=True)
    ep = lc.add_parser("epic"); ep.add_argument("--profile"); ep.add_argument("--initiative", required=True); ep.add_argument("--repo")
    ep.add_argument("--fix-attempts", type=int, default=None); ep.add_argument("--dry-run", action="store_true"); ep.set_defaults(fn=_route_launch, graph="epic")
    de = lc.add_parser("decompose"); de.add_argument("--profile"); de.add_argument("--idea", required=True); de.add_argument("--initiative-id", required=True)
    de.add_argument("--dry-run", action="store_true"); de.set_defaults(fn=_route_launch, graph="decompose")
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
