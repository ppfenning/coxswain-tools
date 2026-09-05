"""cox — the coxswain's operator tools (alias: agent-tools, removed next release)."""

from __future__ import annotations

import argparse
from typing import Optional
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from agent_tools import cleanup, doctor, epic, hud, install, install_exec, plan, provenance, records, release, route, setup_install, setup_screen


def _runs_usage(a: argparse.Namespace) -> int:
    path = Path(a.runs_dir) / f"{a.run_id}.usage.json"
    s = records.usage_summary(records.load_usage(path))
    if a.json:
        print(json.dumps(s, indent=2)); return 0
    print(f"{s['run_id']}: {s['calls']} calls, {s['turns']} turns, ${s['cost_usd']:.2f}, cache-read share {s['cache_read_share']}")
    print(records.format_table([{"role": k, **v} for k, v in s["by_role"].items()], ["role", "calls", "cost_usd", "turns"]))
    print(); print(records.format_table([{"model": k, **v} for k, v in s["by_model"].items()], ["model", "calls", "cost_usd", "turns"]))
    return 0


_SERIES_COLUMNS = ["run", "date", "cartridge_sha", "provider_profile", "calls", "turns", "cost_usd",
                   "cache_share", "tasks_landed", "quarantined", "review_rounds", "cost_per_landed"]


def _runs_series(a: argparse.Namespace) -> int:
    d = Path(a.runs_dir)
    files = {f.name: f.read_text(encoding="utf-8") for pattern in ("*.usage.json", "*:*.json") for f in d.glob(pattern)}
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
        if _run_alive(pid):
            return f"routing: {pidfile.stem} is already running (pid {pid})"
    return None


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

    argv = route.harness_argv(profile, a.graph, run_id, **needs)
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

    with open(log_path, "ab") as log:
        proc = subprocess.Popen(
            argv, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
        )
    pid_path.write_text(str(proc.pid))
    print(f"run {run_id}")
    print(f"pid {pid_path}")
    print(f"log {log_path}")
    return 0


_CORE_PROBE_SCRIPT = '''
import json, sys

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
print(json.dumps(out))
'''


def _run_core_probe(python_path: str, cartridges_dir: str, team: str, skills_roots: list, raw_roots: list | None = None) -> dict:
    """One `python -c` call into the harness venv (spec: setup doctor). Any
    failure to get parseable JSON back is folded into `core_import` as the
    stderr tail, never raised."""
    try:
        proc = subprocess.run(
            [python_path, "-c", _CORE_PROBE_SCRIPT, cartridges_dir, team, *skills_roots],
            capture_output=True, text=True, timeout=60,
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


def _gather_doctor_facts(profile_path: Path) -> dict:
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
        facts.update(_run_core_probe(str(venv_python), expand(profile.get("cartridges_dir", "")), profile.get("team", ""),
                                     [expand(r) for r in roots], raw_roots=roots))
    if profile.get("provider_profile"):
        facts.update(_provider_facts(expand(profile["provider_profile"])))
    if profile.get("workspace_dir"):
        facts.update(_workspace_facts(expand(profile["workspace_dir"])))
    return facts


def _setup_doctor(a: argparse.Namespace) -> int:
    rows = doctor.checks(_gather_doctor_facts(_profile_path(a)))
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
    return dict(
        root=root,
        team=a.team,
        workspace=a.workspace,
        provider_profile=a.provider_profile or f"{root}/agent-cartridges/providers/claude-code.yaml",
        skills_root=a.skills_root or f"{root}/agent-cartridges/skills-plugins",
        uv_on_path=shutil.which("uv") is not None,
        python_exists=python_exists,
        claude_on_path=shutil.which("claude") is not None,
        profile_exists=Path(setup_install.profile_path(config_dir)).exists(),
        force_profile=a.force_profile,
        plugins=a.plugins,
        hook=a.hook,
        config_dir=config_dir,
        claude_settings_path=claude_settings_path,
        assume=a.assume,
    )


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


def _checkout_facts(path: Path) -> dict:
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
    return {"present": True, "tag": tag, "dirty": bool(status.stdout.strip())}


def _gather_checkout_facts(root: Path, components: dict) -> dict:
    """Every manifest component, plus any other git checkout actually
    present under `root` — the `extra` rows `install.rows` can then report."""
    declared = {name: _checkout_facts(root / name) for name in components}
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
               for c, p, i, s in install.rows(manifest, post_facts)]
    print(records.format_table(display, ["component", "pinned_tag", "installed_tag", "status"]))
    return 0 if all(result["exit"] in (0, None) for result in results) else 2


def _install(a: argparse.Namespace) -> int:
    manifest_path = Path(a.manifest) if a.manifest else Path(a.root) / "coxswain" / "manifest.toml"
    manifest = _load_manifest(manifest_path)
    if manifest is None:
        print(f"refusing: no manifest at {manifest_path}")
        return 2
    root = Path(a.root)
    facts = {
        "root": str(root),
        "checkouts": _gather_checkout_facts(root, manifest.get("components", {})),
        "provider_cli_on_path": shutil.which(_manifest_provider_command(manifest, a.provider)) is not None,
    }
    options = {"provider": a.provider, "with": a.with_ or [], "root": str(root), "team": a.team, "workspace": a.workspace}
    steps = install.plan(manifest, facts, options)
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


def _versions(a: argparse.Namespace) -> int:
    manifest_path = Path(a.manifest) if a.manifest else Path(a.root or ".") / "coxswain" / "manifest.toml"
    manifest = _load_manifest(manifest_path)
    if manifest is None:
        print(f"refusing: no manifest at {manifest_path}")
        return 2
    root = Path(a.root) if a.root else manifest_path.resolve().parent.parent
    facts = {"root": str(root), "checkouts": _gather_checkout_facts(root, manifest.get("components", {})),
              "provider_cli_on_path": False}
    display = [{"component": c, "pinned_tag": p or "", "installed_tag": i or "", "status": s}
               for c, p, i, s in install.rows(manifest, facts)]
    print(records.format_table(display, ["component", "pinned_tag", "installed_tag", "status"]))
    return 0


def _remote_tags(repo: str) -> Optional[list[str]]:
    """The tags on `repo`'s GitHub remote, or None when git could not read the
    remote. None is not `[]`: the planner refuses on None, so an unreachable or
    misnamed remote can never look like a clean one."""
    result = subprocess.run(["git", "ls-remote", "--tags", f"https://github.com/{repo}.git"],
                             capture_output=True, text=True)
    return None if result.returncode != 0 else release.parse_ls_remote(result.stdout)


_RELEASE_DETAIL = {
    "refuse": lambda step: step["detail"],
    "tag": lambda step: f"{step['repo']} -> {step['tag']}",
    "bump_manifest": lambda step: f"{step['from']} -> {step['to']}",
    "notes": lambda step: step["path"],
    "tag_self": lambda step: step["tag"],
}


def _release_detail(step: dict) -> str:
    """One line of detail per step kind; an unknown kind shows itself rather than raising."""
    fmt = _RELEASE_DETAIL.get(step["kind"])
    return fmt(step) if fmt is not None else str(step)


def _release(a: argparse.Namespace) -> int:
    if not a.dry_run:
        print("pushing tags is not implemented yet; use --dry-run")
        return 2
    manifest_path = Path(a.manifest) if a.manifest else Path("coxswain") / "manifest.toml"
    manifest = _load_manifest(manifest_path)
    if manifest is None:
        print(f"refusing: no manifest at {manifest_path}")
        return 2
    existing_tags = {name: _remote_tags(spec["repo"]) for name, spec in manifest.get("components", {}).items()
                      if spec.get("repo")}
    steps = release.release_plan(manifest, a.version, existing_tags)
    for step in steps:
        print(f"{step['kind']} {step['component']}: {_release_detail(step)}")
    return 2 if any(step["kind"] == "refuse" for step in steps) else 0


def _setup_tui(a: argparse.Namespace) -> int:
    if not sys.stdin.isatty():
        print("setup: needs a terminal; use setup doctor / setup install / cartridge init directly")
        return 2
    return setup_screen.main(*_setup_fields(a))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cox", description=__doc__)
    sub = p.add_subparsers(dest="group", required=True)

    runs = sub.add_parser("runs", help="what a harness run recorded, and cleaning up after it").add_subparsers(dest="cmd", required=True)
    u = runs.add_parser("usage"); u.add_argument("run_id"); u.add_argument("--runs-dir", default="runs"); u.add_argument("--json", action="store_true"); u.set_defaults(fn=_runs_usage)
    t = runs.add_parser("trace"); t.add_argument("run_id"); t.add_argument("--runs-dir", default="runs"); t.add_argument("--role"); t.add_argument("-v", "--verbose", action="store_true"); t.set_defaults(fn=_runs_trace)
    c = runs.add_parser("clean"); c.add_argument("run_id"); c.add_argument("--repo", required=True); c.add_argument("--worktree-root", default="~/worktrees"); c.add_argument("--apply", action="store_true"); c.set_defaults(fn=_runs_clean)
    se = runs.add_parser("series"); se.add_argument("--runs-dir", default="runs"); se.add_argument("--json", action="store_true"); se.add_argument("--append"); se.set_defaults(fn=_runs_series)

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
    co = lc.add_parser("cos"); co.add_argument("--profile"); co.add_argument("--dry-run", action="store_true")
    co.set_defaults(fn=_route_launch, graph="cos")

    ins = sub.add_parser("install", help="clone/update coxswain components against the manifest")
    ins.add_argument("--root", required=True); ins.add_argument("--manifest"); ins.add_argument("--provider", default="claude-code")
    ins.add_argument("--with", action="append", default=None, dest="with_", metavar="FLAG")
    ins.add_argument("--team"); ins.add_argument("--workspace"); ins.add_argument("--dry-run", action="store_true")
    ins.set_defaults(fn=_install)

    up = sub.add_parser("upgrade", help="fetch and check out newer pinned versions; refuses dirty checkouts")
    up.add_argument("--root", required=True); up.add_argument("--manifest"); up.add_argument("--provider", default="claude-code")
    up.add_argument("--with", action="append", default=None, dest="with_", metavar="FLAG")
    up.add_argument("--team"); up.add_argument("--workspace"); up.add_argument("--to"); up.add_argument("--dry-run", action="store_true")
    up.set_defaults(fn=_upgrade)

    ver = sub.add_parser("versions", help="component versions against the manifest")
    ver.add_argument("--root"); ver.add_argument("--manifest"); ver.set_defaults(fn=_versions)

    rel = sub.add_parser("release", help="the lockstep tag/bump-manifest/notes plan across coxswain's manifest")
    rel.add_argument("version"); rel.add_argument("--manifest"); rel.add_argument("--dry-run", action="store_true")
    rel.set_defaults(fn=_release)

    setup_p = sub.add_parser("setup", help="does this machine's profile actually work")
    setup_p.add_argument("--profile")
    setup_p.set_defaults(fn=_setup_tui)
    su = setup_p.add_subparsers(dest="cmd", required=False)
    sd = su.add_parser("doctor"); sd.add_argument("--profile"); sd.add_argument("--json", action="store_true"); sd.set_defaults(fn=_setup_doctor)
    si = su.add_parser("install")
    si.add_argument("--root", required=True); si.add_argument("--team", required=True); si.add_argument("--workspace", required=True)
    si.add_argument("--provider-profile"); si.add_argument("--skills-root"); si.add_argument("--assume", default="a", choices=("a", "r"))
    si.add_argument("--plugins", action="store_true"); si.add_argument("--hook", action="store_true")
    si.add_argument("--force-profile", action="store_true"); si.add_argument("--dry-run", action="store_true")
    si.set_defaults(fn=_setup_install)
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
