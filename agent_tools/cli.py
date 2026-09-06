"""cox — the coxswain's operator tools (alias: agent-tools, removed next release)."""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import yaml

from agent_tools import (
    cleanup,
    doctor,
    epic,
    install,
    install_exec,
    land,
    leader,
    notify,
    pacing,
    plan,
    provenance,
    records,
    release,
    release_check,
    release_check_cli,
    release_check_manifest,
    release_check_notes,
    route,
    runs_detail,
    runs_detail_screen,
    runs_top,
    runs_top_screen,
    setup_install,
    setup_screen,
    usage_window,
)
from agent_tools import runs as runs_module


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


def _runs_top(a: argparse.Namespace) -> int:
    heartbeat_minutes = _leader_heartbeat_minutes()
    if a.once:
        leader_state = runs_top_screen.leader_now(a.runs_dir, heartbeat_minutes)
        print("\n".join(runs_top.render(runs_top_screen.rows_now(a.runs_dir, heartbeat_minutes), 120, leader_state)))
        return 0
    if not sys.stdin.isatty():
        print("runs top: needs a terminal; use --once")
        return 2
    return runs_top_screen.main(a.runs_dir, a.interval, heartbeat_minutes)


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
                            heartbeat_minutes=_leader_heartbeat_minutes())


def _runs_detail(a: argparse.Namespace) -> int:
    d = runs_detail.detail(**runs_detail_screen.facts_for(a.runs_dir, a.run_id))
    if a.json:
        print(json.dumps(dataclasses.asdict(d)))
    else:
        print("\n".join(runs_detail.render(d, 120)))
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


def _land_record(run_id: str, task: str | None) -> tuple[dict, str] | None:
    tasks_root = Path("runs") / run_id / "tasks"
    matches = sorted(tasks_root.glob(f"*/{task}.json" if task else "*/*.json"))
    if len(matches) != 1:
        return None
    path = matches[0]
    record = json.loads(path.read_text(encoding="utf-8"))
    record.setdefault("run", run_id)
    record.setdefault("task", path.stem)
    record.setdefault("phase", path.parent.name)
    return record, str(path)


def _land_branches(repo: Path, record: dict, default_branch: str) -> dict[str, list[str]]:
    """Commit subjects ahead of `default_branch`, per candidate branch, with
    merge commits already excluded by `git` itself (`--no-merges`) rather
    than guessed from a subject's wording."""
    candidates = [f"agents/{record['run']}/{record['task']}", f"epic/{record.get('initiative')}/{record['phase']}"]
    branches: dict[str, list[str]] = {}
    for b in candidates:
        out = subprocess.run(["git", "-C", str(repo), "log", "--no-merges", "--format=%s", f"{default_branch}..{b}"], capture_output=True, text=True)
        if out.returncode == 0:
            branches[b] = [line for line in out.stdout.splitlines() if line]
    return branches


def _land_enrich(steps: list[dict], *, path: str, worktree_root: str) -> list[dict]:
    """Steps enriched with what only the edge knows: the record's own file
    path for `mark_done`, and the configured worktree root for `clean`."""
    def enrich(step: dict) -> dict:
        if step["kind"] == "mark_done":
            return {**step, "path": path}
        if step["kind"] == "clean":
            return {**step, "worktree_root": worktree_root}
        return step
    return [enrich(s) for s in steps]


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
    if kind == "checks":
        r = subprocess.run(step["argv"], cwd=repo, capture_output=True, text=True)
        return r.returncode == 0, " ".join(step["argv"])
    if kind == "push":
        r = subprocess.run(["git", "-C", str(repo), "push", "-u", "origin", step["branch"]], capture_output=True, text=True)
        return r.returncode == 0, (step["branch"] if r.returncode == 0 else r.stderr.strip() or r.stdout.strip())
    if kind == "pr_create":
        r = subprocess.run(["gh", "pr", "create", "--title", step["title"], "--body", step["body"]], cwd=repo, capture_output=True, text=True)
        return r.returncode == 0, (r.stdout.strip() or r.stderr.strip())
    if kind == "wait_checks":
        return _wait_checks(repo, float(step.get("timeout_s", 300)))
    if kind == "merge":
        r = subprocess.run(["gh", "pr", "merge", "--squash", "--delete-branch"], cwd=repo, capture_output=True, text=True)
        return r.returncode == 0, (r.stdout.strip() or r.stderr.strip())
    if kind == "clean":
        p = cleanup.plan_cleanup(run_id=step["run"], worktrees=cleanup.git_worktrees(repo), branches=cleanup.git_branches(repo), worktree_root=step["worktree_root"])
        return True, "; ".join(cleanup.apply_cleanup(repo, p, dry_run=False))
    if kind == "mark_done":
        record_path = Path(step["path"])
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record_path.write_text(json.dumps({**record, "landed": True}, indent=2), encoding="utf-8")
        return True, f"{step['task']} marked landed at {record_path}"
    return False, f"unknown step {kind!r}"


def _wait_checks(repo: Path, timeout_s: float, sleep=time.sleep, now=time.monotonic) -> tuple[bool, str]:
    """Poll `gh pr checks` until a check reports, one is green, or `timeout_s` passes with none registered."""
    started = now()
    while True:
        r = subprocess.run(["gh", "pr", "checks", "--watch", "--fail-fast"], cwd=repo, capture_output=True, text=True)
        output = (r.stdout or "") + (r.stderr or "")
        decision = land.wait_decision(r.returncode, output, now() - started, timeout_s)
        if decision == "green":
            return True, "green"
        if decision == "retry":
            sleep(10)
            continue
        if decision == "timeout":
            return False, f"no checks registered within {timeout_s:.0f}s"
        return False, output.strip()


def _repo_is_dirty(repo: Path) -> bool:
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True)
    return bool(status.stdout.strip())


def _runs_land(a: argparse.Namespace) -> int:
    repo = Path(a.repo).expanduser()
    if a.apply:
        guard_rc = _leader_guard_or_refuse(Path("runs"), _holder_label(a), a.force)
        if guard_rc is not None:
            return guard_rc
    found = _land_record(a.run_id, a.task)
    if found is None:
        print(f"land: expected exactly one task record under runs/{a.run_id}/tasks, found something else")
        return 2
    record, path = found
    default_branch = "main"
    branches = _land_branches(repo, record, default_branch)
    repo_facts = {"venv_python": (repo / ".venv" / "bin" / "python").exists(), "uv_lock": (repo / "uv.lock").exists()}
    steps = _land_enrich(land.land_plan(record, branches, default_branch, repo_facts), path=path, worktree_root=a.worktree_root)
    if not a.apply:
        print(json.dumps(steps, indent=2))
        return 2 if any(s["kind"] == "refuse" for s in steps) else 0
    if _repo_is_dirty(repo):
        print(f"land: refusing, {repo} is dirty")
        return 2
    pr_branch = next((s["onto"] for s in steps if s["kind"] == "cherry_pick"), None)
    if pr_branch is not None and pr_branch in cleanup.git_branches(repo):
        print(f"land: refusing, branch {pr_branch} already exists in {repo}")
        return 2
    for i, step in enumerate(steps):
        if step["kind"] == "refuse":
            print(f"refused: {step['reason']}")
            return 2
        if step["kind"] == "checks":
            try:
                ok, detail = _execute_land_step(repo, step)
            except OSError as exc:
                # A repo whose pytest lives somewhere `subprocess` can't find
                # is a refusal, not a traceback, and it fires before `push`
                # so a failed check never leaves a pushed branch behind.
                print(f"refuse checks: {step['argv'][0]}: {exc}")
                return 2
        else:
            ok, detail = _execute_land_step(repo, step)
        print(f"{step['kind']}: {detail}")
        if not ok:
            remaining = [s["kind"] for s in steps[i + 1:]]
            print("stopped; remaining: " + ", ".join(remaining))
            return 1
        if step["kind"] == "wait_checks" and a.no_merge:
            print("stopping after wait_checks (--no-merge)")
            return 0
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
    pid_paths = sorted((ws / "runs").glob("*.pid"))
    pids = {p.stem: t for p in pid_paths if (t := _read_text_or_none(p)) is not None}
    alive = {rid: (pid := route.parse_pid(t)) is not None and epic.alive(pid) for rid, t in pids.items()}
    started = {rid: _mtime_iso(ws / "runs" / f"{rid}.pid") for rid in pids}
    # the initiative id is the DIRECTORY name: work/<initiative>/<phase>/<task>.md;
    # the edge only reads and names the path parts — route.work_item normalises
    items = _work_items(ws)
    return (profile, "",
            _intake_groups(ws, items),
            route.run_entries(pids, alive, started),
            route.initiative_summaries(items))


def _route_context(a: argparse.Namespace) -> int:
    # The edge is allowed to catch everything _gather_context can raise: this
    # command must never take a session down with it (spec §2). That
    # tolerance is for profile/workspace file reads only — it stops at the
    # gatherer, per charter A6, so a bug in the usage assessment surfaces
    # instead of erasing an otherwise-good docket (run tools-pacing-7).
    try:
        profile, reason, intake, runs, initiatives = _gather_context(_profile_path(a))
    except Exception as exc:
        print(f"routing: context unavailable ({type(exc).__name__}: {exc})")
        return 0
    # Computed once via the gatherer, same reason `route launch` gates on;
    # only when there is a workspace to gather usage files from.
    usage_reason = (
        _usage_assessment(Path(profile["workspace_dir"]).expanduser() / "runs").reason
        if not reason and profile is not None else None
    )
    if a.json:
        doc = route.context_document(profile, intake, runs, initiatives)
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
        print(f"{route.render_context(profile, intake, runs, initiatives)}\nusage: {usage_reason}")
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
        ws = Path(workspace).expanduser()
        rows = _status_rows_for(ws / "runs")
        groups = _intake_groups_for(ws)
        if a.json:
            doc = rows if groups is None else {"runs": rows, "intake": groups}
            print(json.dumps(doc, indent=2))
        else:
            print(route.render_status(rows, groups))
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
    return leader.DEFAULT_HEARTBEAT_MINUTES


def _leader_read_or_refuse(runs_dir: Path):
    """A file present but unreadable or not valid JSON is a refusal, not a free lock
    (charter A6: `leader.read` raises at the edge; here is where that becomes a value).
    Returns `(record_or_none, None)` or `(None, 2)` after printing the reason."""
    try:
        return leader.read(runs_dir), None
    except (OSError, json.JSONDecodeError) as exc:
        print(f"leader: lock file unreadable ({type(exc).__name__}: {exc})")
        return None, 2


def _holder_label(a: argparse.Namespace) -> str:
    return getattr(a, "label", None) or os.environ.get("COX_SESSION_LABEL") or "unlabeled"


def _leader_guard_or_refuse(runs_dir: Path, holder: str, force: bool) -> int | None:
    """Exit code 2 with the refusal printed when another live session holds the loop; None to proceed."""
    record, rc = _leader_read_or_refuse(runs_dir)
    if rc is not None:
        return None
    state = leader.liveness(record, _leader_pid_alive(record), datetime.datetime.now(datetime.UTC), socket.gethostname(), _leader_heartbeat_minutes())
    line = leader.guard(record, holder, state)
    if line is None:
        return None
    print(f"override: {line}" if force else line)
    return None if force else 2


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
    return leader.pid_alive(pid)


def _leader_refuse_dead_pid(explicit_pid: int | None) -> int | None:
    """Refuses only an explicit `--pid`; the `os.getppid()` default is alive by construction."""
    if explicit_pid is None or leader.pid_alive(explicit_pid):
        return None
    print(f"leader pid not alive: {explicit_pid}")
    return 2


def _leader_launched_by(record: dict | None, pid_alive_: bool, now: datetime.datetime, host: str, heartbeat_minutes: int) -> str | None:
    """The label to stamp on a run launched right now: the lock's own holder when it
    reads live, `None` when it reads stale, crashed, or none — never a guess."""
    if leader.liveness(record, pid_alive_, now, host, heartbeat_minutes) != "live":
        return None
    return record.get("session")


def _print_if_stale(record: dict | None, state: str) -> None:
    if record is not None and state in ("stale", "crashed"):
        print(f"leader {state}: {record.get('session', '?')} (pid {record.get('pid', '?')}) on {record.get('host', '?')}")


def _route_leader_take(a: argparse.Namespace) -> int:
    _profile, runs_dir, refuse_rc = _leader_runs_dir_or_refuse(a)
    if refuse_rc is not None:
        return refuse_rc
    session = a.label or "leader"
    pid, host = _leader_identity(a.pid)
    refuse_rc = _leader_refuse_dead_pid(a.pid)
    if refuse_rc is not None:
        return refuse_rc
    heartbeat_minutes = _leader_heartbeat_minutes()
    with leader.locked(runs_dir):
        record, read_rc = _leader_read_or_refuse(runs_dir)
        if read_rc is not None:
            return read_rc
        now = datetime.datetime.now(datetime.UTC)
        alive = _leader_pid_alive(record)
        prior_state = leader.liveness(record, alive, now, host, heartbeat_minutes)
        _print_if_stale(record, prior_state)
        new_record, reason = leader.take(record, session, pid, host, now, heartbeat_minutes, alive, steal=a.steal)
        if new_record is None:
            print(reason)
            return 2
        leader.write(runs_dir, new_record)
    print(f"leader taken: {new_record['session']} (pid {new_record['pid']}) on {new_record['host']}")
    return 0


def _route_leader_beat(a: argparse.Namespace) -> int:
    _profile, runs_dir, refuse_rc = _leader_runs_dir_or_refuse(a)
    if refuse_rc is not None:
        return refuse_rc
    session = a.label or "leader"
    pid, host = _leader_identity(a.pid)
    refuse_rc = _leader_refuse_dead_pid(a.pid)
    if refuse_rc is not None:
        return refuse_rc
    with leader.locked(runs_dir):
        record, read_rc = _leader_read_or_refuse(runs_dir)
        if read_rc is not None:
            return read_rc
        new_record, reason = leader.beat(record, session, pid, host, datetime.datetime.now(datetime.UTC), run_id=a.run)
        if new_record is None:
            print(reason)
            return 2
        leader.write(runs_dir, new_record)
    print(f"leader heartbeat: {new_record['session']}")
    return 0


def _route_leader_release(a: argparse.Namespace) -> int:
    _profile, runs_dir, refuse_rc = _leader_runs_dir_or_refuse(a)
    if refuse_rc is not None:
        return refuse_rc
    session = a.label or "leader"
    pid, host = _leader_identity(a.pid)
    refuse_rc = _leader_refuse_dead_pid(a.pid)
    if refuse_rc is not None:
        return refuse_rc
    with leader.locked(runs_dir):
        record, read_rc = _leader_read_or_refuse(runs_dir)
        if read_rc is not None:
            return read_rc
        _new_record, reason = leader.release(record, session, pid, host)
        if reason:
            print(reason)
            return 2
        leader.write(runs_dir, None)
    print(f"leader released: {record['session']}")
    return 0


def _route_leader_status(a: argparse.Namespace) -> int:
    _profile, runs_dir, refuse_rc = _leader_runs_dir_or_refuse(a)
    if refuse_rc is not None:
        return refuse_rc
    record, read_rc = _leader_read_or_refuse(runs_dir)
    if read_rc is not None:
        return read_rc
    now = datetime.datetime.now(datetime.UTC)
    alive = _leader_pid_alive(record)
    state = leader.liveness(record, alive, now, socket.gethostname(), _leader_heartbeat_minutes())
    if not a.json:
        _print_if_stale(record, state)
    if a.json:
        print(json.dumps({**(record or {}), "state": state}, indent=2))
    elif record is None:
        print("leader: none")
    else:
        print(f"leader: {record['session']} (pid {record['pid']}) on {record['host']} — {state}")
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


def _route_file_from_intake(a: argparse.Namespace, ws: Path) -> int:
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
    try:
        mapping = route.initiative_files(entry["title"], intake_body, fields.get("repo", ""), phase=a.phase)
    except ValueError as exc:
        print(f"routing: {exc}")
        return 2
    slug = route.slugify(entry["title"])
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
    return 0


def _route_file(a: argparse.Namespace) -> int:
    profile, rc = _resolve_profile_or_refuse(a)
    if rc is not None:
        return rc
    ws = Path(profile["workspace_dir"]).expanduser()
    if a.from_intake:
        return _route_file_from_intake(a, ws)
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
    guard_rc = _leader_guard_or_refuse(runs_dir, _holder_label(a), a.force)
    if guard_rc is not None:
        return guard_rc
    usage_code, usage_lines = route.launch_gate(_usage_assessment(runs_dir), a.force)
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
    return facts


def _setup_doctor(a: argparse.Namespace) -> int:
    repo = Path(a.repo).expanduser() if a.repo else Path.cwd()
    rows = doctor.checks(_gather_doctor_facts(_profile_path(a), repo))
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
        "provider_profile": a.provider_profile or f"{root}/agent-cartridges/providers/claude-code.yaml",
        "skills_root": a.skills_root or f"{root}/agent-cartridges/skills-plugins",
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
}


def _release_detail(step: dict) -> str:
    """One line of detail per step kind; an unknown kind shows itself rather than raising."""
    fmt = _RELEASE_DETAIL.get(step["kind"])
    return fmt(step) if fmt is not None else str(step)


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
    ref_rc, ref_out = run(["git", "-C", directory, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], None)
    default = ref_out.strip().rsplit("/", 1)[-1] if ref_rc == 0 and ref_out.strip() else "main"
    if current != default:
        return False, f"{directory} is on {current}, not {default}"
    return True, ""


def _release_execute(steps: list[dict], version: str, root: str, overrides: dict, umbrella: str, run) -> int:
    """Runs `steps` for real, through `run`. Refuses outright, before `run`
    is ever called, when the plan still carries a `bump_manifest` step: that
    step only happens by bumping and committing the manifest by hand, so a
    plan built from a not-yet-bumped manifest must never tag and push —
    pushed tags cannot be recalled, and a printed line is not the same as
    the manifest actually saying the new version. Otherwise every checkout
    that will be tagged — every component, plus the umbrella when
    `tag_self` is in the plan — and the umbrella's release note are all
    checked before a single tag is made. Then each `tag` step's tag and
    push run in turn, and `tag_self` tags and pushes the umbrella. One line
    per step; the first failure stops the rest."""
    refusal = next((s for s in steps if s["kind"] == "refuse"), None)
    if refusal is not None:
        print(f"refuse {refusal['component']}: {refusal['detail']}")
        return 2

    if any(s["kind"] == "bump_manifest" for s in steps):
        print("refuse manifest: bump and commit the manifest to this version by hand before executing this release")
        return 2

    tag_checkouts = [(s["component"], release.component_dir(root, s["component"], overrides))
                      for s in steps if s["kind"] == "tag"]
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

    for step in steps:
        kind = step["kind"]
        if kind == "tag":
            directory = release.component_dir(root, step["component"], overrides)
            tag_rc, tag_out = run(release.tag_argv(directory, version), None)
            if tag_rc != 0:
                print(f"FAILED tag {step['component']}: {tag_out.strip()}")
                return 2
            push_rc, push_out = run(release.push_argv(directory, version), None)
            if push_rc != 0:
                print(f"FAILED push {step['component']}: {push_out.strip()}")
                return 2
            print(f"tag {step['component']}: {step['tag']}")
        elif kind == "notes":
            print(f"notes notes: {step['path']}")
        elif kind == "note":
            print(f"note {step['component']}: {step['detail']}")
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
    facts = release_check.facts_plan(root, manifest)
    drifts = release_check.run_checks(facts)
    existing_tags = {name: _remote_tags(spec["repo"]) for name, spec in manifest.get("components", {}).items()
                      if spec.get("repo")}
    steps = release.gate(drifts, a.allow_doc_drift) + release.release_plan(manifest, a.version, existing_tags)
    if a.dry_run:
        for step in steps:
            print(f"{step['kind']} {step['component']}: {_release_detail(step)}")
        return 2 if any(step["kind"] == "refuse" for step in steps) else 0
    overrides = dict(pair.split("=", 1) for pair in (a.checkout or []))
    umbrella = a.umbrella or str(Path(root) / "coxswain")
    return _release_execute(steps, a.version, root, overrides, umbrella, _real_run)


def _release_check(a: argparse.Namespace) -> int:
    manifest_path = Path(a.manifest) if a.manifest else Path("coxswain") / "manifest.toml"
    manifest = _load_manifest(manifest_path)
    if manifest is None:
        print(f"refusing: no manifest at {manifest_path}")
        return 2
    plan = release_check.facts_plan(a.root or ".", manifest)
    facts = {
        **plan,
        **release_check_cli.gather_cli_facts(a.root or ".", _real_run),
        **release_check_manifest.gather_manifest_facts(manifest, str(manifest_path), plan["component_docs"], plan["release_notes"]),
        **release_check_notes.gather_notes_facts(a.root or ".", manifest, subprocess.run),
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
    return home_screen.main(runs_dir, workspace / "work", workspace / "intake", str(plugin_root or ""))


def _setup_tui(a: argparse.Namespace) -> int:
    if not sys.stdin.isatty():
        print("setup: needs a terminal; use setup doctor / setup install / cartridge init directly")
        return 2
    return setup_screen.main(*_setup_fields(a))


_USAGE_ASSESS_EXIT = {"go": 0, "go_degraded": 0, "hold": 3, "stop": 4}


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
    )


def _usage_assessment(runs_dir) -> pacing.Assessment:
    """Computed once via the gatherer and shared by every surface that
    narrates it: `usage assess`, `route context`'s docket line, and `route
    launch`'s gate all call this so the same window yields the same reason.
    """
    now = datetime.datetime.now(datetime.UTC)
    window = usage_window.gather(runs_dir, now)
    policy = _resolved_pacing_policy(Path(runs_dir))
    return pacing.assess(window, policy, now)


def _usage_assess(a: argparse.Namespace) -> int:
    result = _usage_assessment(a.runs_dir)
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

    runs_p = sub.add_parser(
        "runs", help="what a harness run recorded, and cleaning up after it",
        description="What a harness run recorded, and cleaning up after it.",
        epilog="examples:\n  cox runs land <run> --repo PATH --apply\n  cox runs usage <run> --json\n  cox runs detail <run> --json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    runs_p.set_defaults(fn=_bare_group(runs_p))
    runs = runs_p.add_subparsers(dest="cmd", required=False)
    u = runs.add_parser("usage", help="usage stats and cost for one run"); u.add_argument("run_id"); u.add_argument("--runs-dir", default="runs"); u.add_argument("--json", action="store_true"); u.set_defaults(fn=_runs_usage)
    t = runs.add_parser("trace", help="the tool-call trace for one run"); t.add_argument("run_id"); t.add_argument("--runs-dir", default="runs"); t.add_argument("--role"); t.add_argument("-v", "--verbose", action="store_true"); t.set_defaults(fn=_runs_trace)
    c = runs.add_parser("clean", help="delete a run's worktree and branches locally"); c.add_argument("run_id"); c.add_argument("--repo", required=True); c.add_argument("--worktree-root", default="~/worktrees"); c.add_argument("--apply", action="store_true"); c.set_defaults(fn=_runs_clean)
    la = runs.add_parser("land", help="merge a run's branch into the target repo"); la.add_argument("run_id"); la.add_argument("--repo", required=True); la.add_argument("--task"); la.add_argument("--label"); la.add_argument("--force", action="store_true", help="land despite a foreign live leader")
    la.add_argument("--worktree-root", default="~/worktrees"); la.add_argument("--apply", action="store_true"); la.add_argument("--no-merge", action="store_true"); la.set_defaults(fn=_runs_land)
    se = runs.add_parser("series", help="per-run summary rows across a runs directory"); se.add_argument("--runs-dir", default="runs"); se.add_argument("--json", action="store_true"); se.add_argument("--append"); se.set_defaults(fn=_runs_series)
    ev = runs.add_parser("events", help="poll a run's log for structured events"); ev.add_argument("--runs-dir", default="runs"); ev.add_argument("--follow", action="store_true"); ev.add_argument("--json", action="store_true"); ev.set_defaults(fn=_runs_events)
    tp = runs.add_parser("top", help="live table of runs in flight; --once prints it and exits"); tp.add_argument("--runs-dir", default="runs"); tp.add_argument("--interval", type=float, default=3); tp.add_argument("--once", action="store_true"); tp.set_defaults(fn=_runs_top)
    no = runs.add_parser("notify", help="desktop notifications for exits, quarantines, budget stops and cost"); no.add_argument("--runs-dir", default="runs"); no.add_argument("--once", action="store_true"); no.add_argument("--interval", type=float, default=10); no.set_defaults(fn=_runs_notify)
    de = runs.add_parser("detail", help="one run's timeline, objection and last tool calls"); de.add_argument("run_id"); de.add_argument("--runs-dir", default="runs"); de.add_argument("--json", action="store_true"); de.set_defaults(fn=_runs_detail)

    usage_p = sub.add_parser(
        "usage", help="spend pacing against the ceiling for the current window",
        description="Spend pacing against the ceiling for the current window.",
        epilog="examples:\n  cox usage assess\n  cox usage assess --json --runs-dir runs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    usage_p.set_defaults(fn=_bare_group(usage_p))
    us = usage_p.add_subparsers(dest="cmd", required=False)
    ua = us.add_parser("assess", help="the pacing verdict for the current spend window"); ua.add_argument("--json", action="store_true"); ua.add_argument("--runs-dir", default="runs"); ua.set_defaults(fn=_usage_assess)

    epic_p = sub.add_parser(
        "epic", help="watch a detached run",
        description="Watch a detached run.",
        epilog="examples:\n  cox epic watch RUN.pid --log RUN.log\n  cox epic watch RUN.pid --json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    epic_p.set_defaults(fn=_bare_group(epic_p))
    e = epic_p.add_subparsers(dest="cmd", required=False)
    w = e.add_parser("watch", help="poll a detached run's pidfile until it exits"); w.add_argument("pidfile"); w.add_argument("--log"); w.add_argument("--max-seconds", type=float, default=570); w.add_argument("--interval", type=float, default=20); w.add_argument("--json", action="store_true"); w.set_defaults(fn=_epic_watch)

    plan_p = sub.add_parser(
        "plan", help="visual plans through the local bridge",
        description="Visual plans through the local bridge.",
        epilog="examples:\n  cox plan serve work/<id> --kind plan\n  cox plan serve work/<id> --check",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    plan_p.set_defaults(fn=_bare_group(plan_p))
    pl = plan_p.add_subparsers(dest="cmd", required=False)
    s = pl.add_parser("serve", help="serve a visual plan through the local bridge"); s.add_argument("dir"); s.add_argument("--kind", default="plan"); s.add_argument("--check", action="store_true"); s.add_argument("--no-open", action="store_true"); s.set_defaults(fn=_plan_serve)

    route_p = sub.add_parser(
        "route", help="file work for the harness, and see what is queued or running",
        description="File work for the harness, and see what is queued or running.",
        epilog="examples:\n  cox route launch epic --initiative work/<id> --repo PATH\n  cox route status --profile PATH",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    route_p.set_defaults(fn=_bare_group(route_p))
    r = route_p.add_subparsers(dest="cmd", required=False)
    ctx = r.add_parser("context", help="the routing profile's resolved context"); ctx.add_argument("--profile"); ctx.add_argument("--json", action="store_true"); ctx.set_defaults(fn=_route_context)
    st = r.add_parser("status", help="what is queued or running for this profile"); st.add_argument("--profile"); st.add_argument("--json", action="store_true"); st.set_defaults(fn=_route_status)
    f = r.add_parser("file", help="file a new ticket for the harness"); f.add_argument("--profile"); f.add_argument("--repo"); f.add_argument("--title")
    f.add_argument("--body"); f.add_argument("--phase", default="build"); f.add_argument("--intake", action="store_true")
    f.add_argument("--from-intake", help="link and file an existing intake file's initiative, then retire it"); f.set_defaults(fn=_route_file)
    ld = r.add_parser("leader", help="the leader lock for the landing loop (runs/leader.json)")
    ld.set_defaults(fn=_bare_group(ld))
    lds = ld.add_subparsers(dest="leader_cmd", required=False)
    lt = lds.add_parser("take", help="take the leader lock if no live leader holds it")
    lt.add_argument("--profile"); lt.add_argument("--label"); lt.add_argument("--pid", type=int, help="the durable pid that owns the loop (default: the parent process)"); lt.add_argument("--steal", action="store_true"); lt.set_defaults(fn=_route_leader_take)
    lb = lds.add_parser("beat", help="refresh the leader lock's heartbeat")
    lb.add_argument("--profile"); lb.add_argument("--label"); lb.add_argument("--pid", type=int, help="the durable pid that owns the loop (default: the parent process)"); lb.add_argument("--run"); lb.set_defaults(fn=_route_leader_beat)
    lr = lds.add_parser("release", help="release the leader lock this session holds")
    lr.add_argument("--profile"); lr.add_argument("--label"); lr.add_argument("--pid", type=int, help="the durable pid that owns the loop (default: the parent process)"); lr.set_defaults(fn=_route_leader_release)
    lst = lds.add_parser("status", help="the leader lock's holder and computed state")
    lst.add_argument("--profile"); lst.add_argument("--json", action="store_true"); lst.set_defaults(fn=_route_leader_status)
    lc = r.add_parser("launch", help="run one of the harness's graphs directly").add_subparsers(dest="graph", required=True)
    ep = lc.add_parser("epic", help="launch the epic graph against a filed initiative"); ep.add_argument("--profile"); ep.add_argument("--initiative", required=True); ep.add_argument("--repo")
    ep.add_argument("--fix-attempts", type=int, default=None); ep.add_argument("--dry-run", action="store_true"); ep.set_defaults(fn=_route_launch, graph="epic")
    de = lc.add_parser("decompose", help="launch the decompose graph against an idea"); de.add_argument("--profile"); de.add_argument("--idea", required=True); de.add_argument("--initiative-id", required=True)
    de.add_argument("--dry-run", action="store_true"); de.set_defaults(fn=_route_launch, graph="decompose")
    co = lc.add_parser("cos", help="launch the cos graph"); co.add_argument("--profile"); co.add_argument("--dry-run", action="store_true")
    co.set_defaults(fn=_route_launch, graph="cos")
    for _launch_parser in (ep, de, co):
        _launch_parser.add_argument("--tier-ceiling", choices=("cheap", "standard", "deep"))
        _launch_parser.add_argument("--effort-ceiling", choices=("low", "high"))
        _launch_parser.add_argument("--force", action="store_true", help="launch despite a usage stop or a foreign live leader")
        _launch_parser.add_argument("--label")

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

    dev = sub.add_parser(
        "dev", help="maintainer commands for the Coxswain repositories",
        description="maintainer commands for the Coxswain repositories; not needed to use Coxswain",
    ).add_subparsers(dest="cmd", required=True)
    rel = dev.add_parser("release", help="the lockstep tag/bump-manifest/notes plan across coxswain's manifest, or (without --dry-run) tags and pushes every component")
    rel.add_argument("version"); rel.add_argument("--manifest"); rel.add_argument("--dry-run", action="store_true")
    rel.add_argument("--root", default="."); rel.add_argument("--checkout", action="append", default=None, metavar="NAME=PATH")
    rel.add_argument("--umbrella")
    rel.add_argument("--allow-doc-drift", dest="allow_doc_drift", metavar="REASON", default=None,
                      help="proceed despite a standing release-check drift, naming why")
    rel.set_defaults(fn=_release)

    relc = dev.add_parser("release-check", help="gather facts and print drifts between the CLI, the manifest, the docs and the release notes")
    relc.add_argument("--manifest"); relc.add_argument("--root", default="."); relc.add_argument("--json", action="store_true")
    relc.set_defaults(fn=_release_check)

    old_rel = sub.add_parser("release", help=argparse.SUPPRESS)
    # swallow every flag the old form took, so the hint prints instead of argparse erroring
    old_rel.add_argument("rest", nargs=argparse.REMAINDER)
    old_rel.set_defaults(fn=_release_moved)

    home_p = sub.add_parser("home", help="the live dashboard: runs, leader, backlog")
    home_p.add_argument("--profile")
    home_p.set_defaults(fn=_home)

    setup_p = sub.add_parser(
        "setup", help="does this machine's profile actually work",
        description="Does this machine's profile actually work.",
        epilog="examples:\n  cox setup doctor --profile PATH\n  cox setup install --root PATH --team NAME --workspace PATH",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    setup_p.add_argument("--profile")
    setup_p.set_defaults(fn=_setup_tui)
    su = setup_p.add_subparsers(dest="cmd", required=False)
    sd = su.add_parser("doctor", help="check this machine's profile against what it needs"); sd.add_argument("--profile"); sd.add_argument("--repo", help="target repo to check for a .agent/cartridge.yaml overlay (default: cwd)"); sd.add_argument("--json", action="store_true"); sd.set_defaults(fn=_setup_doctor)
    si = su.add_parser("install", help="clone components and write a profile for this machine")
    si.add_argument("--root", required=True); si.add_argument("--team", required=True); si.add_argument("--workspace", required=True)
    si.add_argument("--provider-profile"); si.add_argument("--skills-root"); si.add_argument("--assume", default="a", choices=("a", "r"))
    si.add_argument("--plugins", action="store_true"); si.add_argument("--hook", action="store_true")
    si.add_argument("--force-profile", action="store_true"); si.add_argument("--dry-run", action="store_true")
    si.set_defaults(fn=_setup_install)
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
    if not args:
        if sys.stdout.isatty():
            return _home(argparse.Namespace(profile=None))
        return _route_status(argparse.Namespace(profile=None, json=False))
    split = _bare_launcher_split(args)
    head, tail = split if split is not None else (args, [])
    a = build_parser().parse_args(head)
    if not hasattr(a, "fn"):
        return _launcher(a, tail)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
