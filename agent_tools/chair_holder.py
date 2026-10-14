"""Pure verdict on whether a live chair loop holds the chair lease. The caller reads the clock, the store and the pid.

Nothing in `chair_read_lease.lease_record` or `chair.lease_holder` tells a Claude session holder from a chair run loop
holder: both are `session@host:pid`. The caller therefore says which it is with `HolderLease.is_loop`.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Kind = Literal["live_same_host", "live_other_host", "none"]


@dataclass(frozen=True)
class HolderLease:
    label: str
    pid: int
    host: str
    expires_at: datetime
    is_loop: bool


@dataclass(frozen=True)
class HolderVerdict:
    kind: Kind
    label: str
    pid: int
    host: str
    tick_age_s: float | None
    reason: str = ""


def tick_age_s(now: datetime, last_tick: datetime | None) -> float | None:
    """Seconds from the loop's last tick to `now`; None when it never ticked."""
    return None if last_tick is None else (now - last_tick).total_seconds()


def _verdict(lease: HolderLease, kind: Kind, age: float | None, reason: str = "") -> HolderVerdict:
    return HolderVerdict(kind, lease.label, lease.pid, lease.host, age, reason)


def chair_holder_verdict(
    lease: HolderLease,
    this_host: str,
    now: datetime,
    last_tick: datetime | None,
    pid_alive: bool | None,
    max_tick_age_s: float,
) -> HolderVerdict:
    """`pid_alive` is None when the holder is on another host. A same-host holder is live only when `pid_alive` is True."""
    age = tick_age_s(now, last_tick)
    if lease.expires_at <= now:
        return _verdict(lease, "none", age, "expired")
    if not lease.is_loop:
        return _verdict(lease, "none", age, "not a loop")
    if lease.host == this_host:
        return _verdict(lease, "live_same_host", age) if pid_alive is True else _verdict(lease, "none", age, "dead")
    fresh = age is not None and age <= max_tick_age_s
    return _verdict(lease, "live_other_host", age) if fresh else _verdict(lease, "none", age, "stale tick")


def format_holder_verdict(v: HolderVerdict) -> str:
    """One line, no newline."""
    age = "never" if v.tick_age_s is None else f"{v.tick_age_s:.0f}s ago"
    who = f"{v.label} pid {v.pid} on {v.host}, last tick {age}"
    return f"no live chair loop: {v.reason} ({who})" if v.kind == "none" else f"chair loop {who}"
