"""Pure core for `cox home`: Facts in, panes and step's effect out. No curses, subprocess, or file reads."""

from __future__ import annotations

import dataclasses
import datetime
from dataclasses import dataclass

from agent_tools import runs_top

__all__ = [
    "Drill",
    "Effect",
    "Facts",
    "Intake",
    "Land",
    "Quit",
    "Refuse",
    "Setup",
    "State",
    "Talk",
    "attention_pane",
    "backlog_pane",
    "frame",
    "leader_pane",
    "panel_status",
    "runs_pane",
    "step",
    "time_to_reset",
    "window_pane",
]

_ATTENTION_MARK = "! "
_ELLIPSIS = "…"
_SIDE_BY_SIDE_WIDTH = 160
_COLUMN_COUNT = 3
OPENING_CONTEXT = "cox route context"
SETUP_ARGV = ("cox", "setup")
_ATTENTION_REASONS = {"exited": ("gate", "l"), "quarantined": ("quarantine", "i"), "budget": ("budget stop", "i")}


@dataclass(frozen=True)
class Facts:
    leader: dict | None
    leader_liveness: str
    runs_rows: tuple[runs_top.Row, ...]
    backlog: dict
    window: dict
    now: float


@dataclass(frozen=True)
class State:
    plugin_dir: str
    leader_liveness: str
    other_holder: str | None
    selected_run: str | None = None
    selected_status: str | None = None
    land_armed: str | None = None


@dataclass(frozen=True)
class Talk:
    plugin_dir: str
    opening: str = OPENING_CONTEXT


@dataclass(frozen=True)
class Setup:
    argv: tuple[str, ...] = SETUP_ARGV


@dataclass(frozen=True)
class Quit:
    pass


@dataclass(frozen=True)
class Refuse:
    holder: str


@dataclass(frozen=True)
class Drill:
    run: str


@dataclass(frozen=True)
class Land:
    run: str
    apply: bool


@dataclass(frozen=True)
class Intake:
    pass


Effect = Talk | Setup | Quit | Refuse | Drill | Land | Intake


def _refused(state: State) -> bool:
    return state.leader_liveness == "live" and bool(state.other_holder)


def step(state: State, key: str) -> tuple[State, Effect | None]:
    """Only 'l' refuses under a foreign live leader, being the one key that writes; it arms an exited run on its first press, applies on a consecutive press on that same run, and disarms the moment the selection differs from what is armed."""
    if state.land_armed is not None and state.land_armed != state.selected_run:
        state = dataclasses.replace(state, land_armed=None)
    if key == "t":
        return state, Talk(state.plugin_dir)
    if key == "s":
        return state, Setup()
    if key == "q":
        return state, Quit()
    if key == "ENTER":
        if state.selected_run is None:
            return state, None
        return state, Drill(state.selected_run)
    if key == "l":
        if _refused(state):
            return state, Refuse(state.other_holder)
        if state.selected_run is None or state.selected_status != "exited":
            return state, None
        apply = state.selected_run == state.land_armed
        armed = None if apply else state.selected_run
        return dataclasses.replace(state, land_armed=armed), Land(state.selected_run, apply)
    if key == "i":
        return state, Intake()
    return state, None


def _cut(line: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(line) <= width:
        return line
    return line[: max(width - 1, 0)] + _ELLIPSIS


def _heartbeat_age(leader: dict | None, now: float) -> float | None:
    """Seconds between `now` and the leader's own `heartbeat_at`, or None with no leader or no parseable timestamp."""
    if leader is None:
        return None
    try:
        heartbeat_at = datetime.datetime.fromisoformat(leader["heartbeat_at"])
    except (KeyError, TypeError, ValueError):
        return None
    return now - heartbeat_at.timestamp()


def panel_status(last_value, age_seconds: float | None, timeout_seconds: float) -> str:
    """fresh within `timeout_seconds`, stale past it with a value, absent with none."""
    if last_value is None:
        return "absent"
    if age_seconds is None or age_seconds > timeout_seconds:
        return "stale"
    return "fresh"


def leader_pane(facts: Facts, width: int) -> tuple[str, ...]:
    holder = (facts.leader or {}).get("session", "none")
    live_runs = any(r.alive for r in facts.runs_rows)
    attention = facts.leader_liveness in ("none", "stale") and live_runs
    mark = _ATTENTION_MARK if attention else ""
    age = _heartbeat_age(facts.leader, facts.now)
    heartbeat = f"{age:.0f}s ago" if age is not None else "n/a"
    lines = (f"{mark}LEADER", f"holder: {holder}  status: {facts.leader_liveness}  heartbeat: {heartbeat}")
    return tuple(_cut(line, width) for line in lines)


def runs_pane(facts: Facts, width: int) -> tuple[str, ...]:
    return tuple(runs_top.render(list(facts.runs_rows), width))


def attention_pane(facts: Facts, width: int) -> tuple[str, ...]:
    lines = tuple(
        f"{row.run}: {_ATTENTION_REASONS[row.status][0]} [{_ATTENTION_REASONS[row.status][1]}]"
        for row in facts.runs_rows
        if row.status in _ATTENTION_REASONS
    )
    return tuple(_cut(line, width) for line in lines)


def backlog_pane(facts: Facts, width: int) -> tuple[str, ...]:
    b = facts.backlog
    counts = f"queued {b.get('queued', 0)}  decomposed {b.get('decomposed', 0)}  landed {b.get('landed', 0)}"
    ready = b.get("ready", {})
    ready_line = "ready: " + (", ".join(f"{k}={v}" for k, v in ready.items()) or "none")
    lines = ("BACKLOG", counts, ready_line)
    return tuple(_cut(line, width) for line in lines)


def time_to_reset(end: datetime.datetime, now: datetime.datetime) -> str:
    return f"{max(int((end - now).total_seconds() // 60), 0)}m"


def window_pane(facts: Facts, width: int) -> tuple[str, ...]:
    w = facts.window
    verdict = f"tier {w.get('tier', '')} effort {w.get('effort_ceiling', '')}"
    spend = f"spent ${w.get('spent_usd', 0):.2f}  reset in {w.get('time_to_reset', '')}"
    reason = w.get("reason", "")
    lines = ("WINDOW", verdict, spend, reason) if reason else ("WINDOW", verdict, spend)
    return tuple(_cut(line, width) for line in lines)


def _columns(width: int, n: int) -> tuple[int, ...]:
    """`n` column widths whose sum plus `n - 1` single-space separators is exactly `width`."""
    base, extra = divmod(width - (n - 1), n)
    return tuple(base + (1 if i < extra else 0) for i in range(n))


def _side_by_side(columns: tuple[tuple[str, ...], ...], col_widths: tuple[int, ...]) -> tuple[str, ...]:
    height = max(len(c) for c in columns)
    padded = [c + ("",) * (height - len(c)) for c in columns]
    return tuple(" ".join(line.ljust(w) for line, w in zip(row, col_widths)) for row in zip(*padded))


def frame(facts: Facts, state: State, width: int) -> tuple[str, ...]:
    """Layout: runs pane always full width; the other three stack above it under `_SIDE_BY_SIDE_WIDTH`, else sit side by side summing to `width` exactly."""
    runs = runs_pane(facts, width)
    if width >= _SIDE_BY_SIDE_WIDTH:
        widths = _columns(width, _COLUMN_COUNT)
        top = _side_by_side(
            (leader_pane(facts, widths[0]), backlog_pane(facts, widths[1]), window_pane(facts, widths[2])),
            widths,
        )
    else:
        top = (*leader_pane(facts, width), *backlog_pane(facts, width), *window_pane(facts, width))
    return (*top, *runs)
