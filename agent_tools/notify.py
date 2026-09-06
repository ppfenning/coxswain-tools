"""Desktop notifications folded from the runs event stream: `notifications`, `notify_argv`, `fold` pure; `run_loop` the notify-send edge."""

from __future__ import annotations

import datetime
import json
import shutil
import socket
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_tools import leader
from agent_tools.events import Event, poll

__all__ = ["DEFAULT_POLICY", "Notification", "fold", "leader_notifications", "notifications", "notify_argv", "run_loop"]

_LEADER_LOST_HEARTBEAT = "loop leader lost its heartbeat"

DEFAULT_POLICY = {
    "kinds": ["run_exited", "task_quarantined", "budget_stop", "run_exited_cost"],
    "min_cost_usd": 0.0,
}

_INITIAL_STATE = {"log_lines_seen": 0, "trace_names_seen": frozenset(), "emitted_exit": False, "emitted_cost": False}


@dataclass(frozen=True)
class Notification:
    title: str
    body: str
    urgency: str  # one of "low", "normal", "critical"


def notifications(events: Sequence[Event], policy: Mapping | None = None) -> list[Notification]:
    """Pure: the notifications `policy["kinds"]` allows."""
    policy = policy if policy is not None else DEFAULT_POLICY
    kinds = set(policy.get("kinds", DEFAULT_POLICY["kinds"]))
    min_cost_usd = policy.get("min_cost_usd", DEFAULT_POLICY["min_cost_usd"])
    out: list[Notification] = []
    for e in events:
        if e.kind not in kinds:
            continue
        if e.kind == "run_exited":
            out.append(Notification(f"{e.run} exited", f"{e.detail.get('quarantined', 0)} quarantined", "normal"))
        elif e.kind == "task_quarantined":
            body = f"{e.detail.get('task', '')}: {e.detail.get('reason', '')}"
            out.append(Notification(f"{e.run} task quarantined", body, "critical"))
        elif e.kind == "budget_stop":
            # No detail to quote: events.py matches a substring, and the driver
            # that prints the line is not in this checkout. States the fact.
            out.append(Notification(f"{e.run} budget stop", "spend reached the run's budget_usd", "critical"))
        elif e.kind == "run_exited_cost":
            cost = e.detail.get("cost_usd")
            if cost is not None and cost >= min_cost_usd:
                out.append(Notification(f"{e.run} cost", f"${cost:.2f}", "low"))
    return out


def leader_notifications(previous_state: str | None, current_state: str, any_alive: bool) -> list[Notification]:
    """Pure: one critical notice exactly on a live-to-stale/crashed/none transition
    while at least one run is still alive; recovery and quiet leaders stay silent."""
    if previous_state == "live" and current_state in ("stale", "crashed", "none") and any_alive:
        return [Notification("loop leader", _LEADER_LOST_HEARTBEAT, "critical")]
    return []


def _any_run_alive(states: Mapping[str, dict]) -> bool:
    """Pure: whether any tracked run has not yet logged its `run_exited` line —
    the same `emitted_exit` state `events.poll` already keeps, not a fresh pid probe.
    Run-pid monitoring is each leader's own job; this only reads what it already wrote."""
    return any(not s.get("emitted_exit", False) for s in states.values())


def notify_argv(n: Notification) -> list[str]:
    """Pure: the notify-send argv for one notification."""
    return ["notify-send", "-u", n.urgency, "-a", "cox", n.title, n.body]


def fold(states: Mapping[str, dict], batches: Mapping[str, tuple], policy: Mapping | None = None
         ) -> tuple[list[Notification], dict[str, dict]]:
    """Pure: folds one new `(log_lines, trace_names, usage)` batch per run     through `events.poll` into `notifications` and the next per-run state."""
    new_states = dict(states)
    out: list[Notification] = []
    for run, (lines, names, usage) in batches.items():
        events, next_state = poll(run, states.get(run, _INITIAL_STATE), lines, names, usage)
        new_states[run] = next_state
        out.extend(notifications(events, policy))
    return out, new_states


def _read_complete_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    return lines[:-1] if lines and not lines[-1].endswith("\n") else lines


def _trace_names(root: Path, run: str) -> list[str]:
    trace_dir = root / f"{run}-trace"
    return sorted(p.name for p in trace_dir.glob("*.jsonl")) if trace_dir.exists() else []


def _usage(root: Path, run: str, already_emitted: bool):
    path = root / f"{run}.usage.json"
    if already_emitted or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _load_states(path: Path) -> dict[str, dict]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except json.JSONDecodeError:
        raw = {}
    return {run: {**s, "trace_names_seen": frozenset(s.get("trace_names_seen", []))} for run, s in raw.items()}


def _save_states(path: Path, states: Mapping[str, dict]) -> None:
    ser = {run: {**s, "trace_names_seen": sorted(s["trace_names_seen"])} for run, s in states.items()}
    path.write_text(json.dumps(ser), encoding="utf-8")


def _batches(root: Path, states: Mapping[str, dict]) -> dict[str, tuple]:
    out: dict[str, tuple] = {}
    for run in sorted(p.stem for p in root.glob("*.log")):
        state = states.get(run, _INITIAL_STATE)
        complete_lines = _read_complete_lines(root / f"{run}.log")
        seen = state["log_lines_seen"] if len(complete_lines) >= state["log_lines_seen"] else 0
        usage = _usage(root, run, already_emitted=state["emitted_cost"])
        out[run] = (complete_lines[seen:], _trace_names(root, run), usage)
    return out


def _leader_state(root: Path, pid_alive, heartbeat_minutes: int) -> str:
    try:
        record = leader.read(root)
    except (OSError, json.JSONDecodeError):
        record = None
    if record is None:
        return "none"
    alive = pid_alive(record["pid"]) if isinstance(record.get("pid"), int) else False
    return leader.liveness(record, alive, datetime.datetime.now(datetime.UTC), socket.gethostname(), heartbeat_minutes)


def run_loop(runs_dir, *, once: bool = False, interval: float = 10, send=None, sleep=time.sleep,
             policy: Mapping | None = None, pid_alive=leader.pid_alive,
             heartbeat_minutes: int = leader.DEFAULT_HEARTBEAT_MINUTES) -> int:
    """Edge: polls `runs_dir`, sending a notification per event `notifications`     names, plus one on a live-to-stale/none leader transition while a run is alive."""
    root = Path(runs_dir)
    state_path = root / ".notify-state.json"
    runner = send if send is not None else subprocess.run
    can_send = send is not None or shutil.which("notify-send") is not None
    printed_fallback = False
    previous_leader_state = None
    while True:
        states = _load_states(state_path)
        notes, new_states = fold(states, _batches(root, states), policy)
        current_leader_state = _leader_state(root, pid_alive, heartbeat_minutes)
        notes = [*notes, *leader_notifications(previous_leader_state, current_leader_state, _any_run_alive(new_states))]
        previous_leader_state = current_leader_state
        for n in notes:
            if can_send:
                runner(notify_argv(n))
            else:
                if not printed_fallback:
                    print("runs notify: notify-send not found; printing notifications instead")
                    printed_fallback = True
                print(f"[{n.urgency}] {n.title}: {n.body}")
        _save_states(state_path, new_states)
        if once:
            return 0
        sleep(interval)
