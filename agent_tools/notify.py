"""Desktop notifications folded from the runs event stream: `notifications`, `notify_argv`, `fold` pure; `run_loop` the notify-send edge."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_tools.events import Event, poll

__all__ = ["DEFAULT_POLICY", "Notification", "fold", "notifications", "notify_argv", "run_loop"]

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
    """Pure: the notifications `policy["kinds"]` allows;"""
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


def run_loop(runs_dir, *, once: bool = False, interval: float = 10, send=None, sleep=time.sleep,
             policy: Mapping | None = None) -> int:
    """Edge: polls `runs_dir`, sending a notification per event `notifications`     names."""
    root = Path(runs_dir)
    state_path = root / ".notify-state.json"
    runner = send if send is not None else subprocess.run
    can_send = send is not None or shutil.which("notify-send") is not None
    printed_fallback = False
    while True:
        states = _load_states(state_path)
        notes, new_states = fold(states, _batches(root, states), policy)
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
