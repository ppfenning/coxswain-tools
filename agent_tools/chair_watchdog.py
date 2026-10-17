"""Pure core of the chair watchdog: status-line parsing, loop-down and idle-on-hold verdicts.

Plain data in, plain data out. No store, notifier, clock or argument parsing here; the clock is a parameter.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

LOOP_DOWN_TITLE = "Chair loop down"
_LANES = re.compile(r"lanes (\d+)/(\d+)")
_NEEDS_ITEM_SPLIT = re.compile(r", (?=RUNAWAY |needs chair: |partial-land: )")
_NEEDS_PREFIXES = ("needs chair: ", "RUNAWAY ", "partial-land: ")
_WAITING_PREFIX = "waiting: "
_NEEDS_PREFIX = "needs chair: "


@dataclass(frozen=True)
class Alert:
    title: str
    body: str


@dataclass(frozen=True)
class StatusLine:
    busy: int
    total: int
    holds: tuple[str, ...]


def _needs_items(part: str) -> tuple[str, ...]:
    """The holds in one needs part. `needs chair: none` is no hold; the `needs chair: ` prefix is dropped."""
    items = _NEEDS_ITEM_SPLIT.split(part)
    return tuple(i.removeprefix(_NEEDS_PREFIX) for i in items if i != "needs chair: none")


def parse_status(line: str) -> StatusLine | None:
    """None when the line has no `lanes B/T` part, so an unreadable line is never an idle tick."""
    parts = line.split(" | ")
    lanes = next((m for m in (_LANES.fullmatch(p) for p in parts) if m), None)
    if lanes is None:
        return None
    waiting = tuple(p.removeprefix(_WAITING_PREFIX) for p in parts if p.startswith(_WAITING_PREFIX))
    needs = tuple(item for p in parts if p.startswith(_NEEDS_PREFIXES) for item in _needs_items(p))
    return StatusLine(int(lanes.group(1)), int(lanes.group(2)), waiting + needs)


def loop_down(holder: str | None, last_beat: datetime | None, now: datetime, stale_minutes: int = 10) -> Alert | None:
    """An absent lease row (`last_beat` None) is loop down with the holder reported as unknown."""
    if last_beat is None:
        return Alert(LOOP_DOWN_TITLE, f"No chair lease row. Holder: {holder or 'unknown'}. Last beat: never.")
    if now - last_beat <= timedelta(minutes=stale_minutes):
        return None
    return Alert(LOOP_DOWN_TITLE, f"Holder {holder or 'unknown'} last beat {last_beat.isoformat()}.")


def idle(rows: Sequence[tuple[datetime, str]], now: datetime, idle_minutes: int = 30) -> Alert | None:
    """Rows older than `idle_minutes` are ignored. An empty window is loop down; otherwise alert only when every
    row parses with zero busy lanes and the union of holds is non-empty."""
    window = [line for ts, line in rows if timedelta(0) <= now - ts <= timedelta(minutes=idle_minutes)]
    if not window:
        return Alert(LOOP_DOWN_TITLE, f"No status row seen in {idle_minutes} min.")
    parsed = [parse_status(line) for line in window]
    if any(p is None or p.busy > 0 for p in parsed):
        return None
    holds = tuple(dict.fromkeys(h for p in parsed if p is not None for h in p.holds))
    if not holds:
        return None
    return Alert(f"Lanes idle {idle_minutes} min", "Held: " + "; ".join(holds))


def evaluate(
    holder: str | None,
    last_beat: datetime | None,
    rows: Sequence[tuple[datetime, str]],
    now: datetime,
    stale_minutes: int = 10,
    idle_minutes: int = 30,
) -> tuple[Alert, ...]:
    """Both checks, with at most one loop-down alert; the heartbeat's wins because it names the holder."""
    alerts = tuple(a for a in (loop_down(holder, last_beat, now, stale_minutes), idle(rows, now, idle_minutes)) if a)
    down = tuple(a for a in alerts if a.title == LOOP_DOWN_TITLE)[:1]
    return down + tuple(a for a in alerts if a.title != LOOP_DOWN_TITLE)
