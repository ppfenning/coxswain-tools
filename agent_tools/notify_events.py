"""Pure detection of push events from one chair tick: the gathered facts and the planned actions in, events out."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agent_tools.notify_core import Event


def _host_login(action: Mapping) -> Event:
    host = str(action.get("host") or "")
    url = str(action.get("url") or "")
    body = (
        f"The claude login on {host} has lapsed. Open the login link."
        if url
        else f"The claude login on {host} has lapsed. Run the claude login on that host."
    )
    return Event("host_login", f"host_login:{host}", f"Login lapsed on {host}", body, url)


def _stall(action: Mapping) -> Event:
    initiative = str(action.get("initiative") or "")
    reason = str(action.get("reason") or "")
    if action.get("cause") == "stalled":
        run = str(action.get("run") or "")
        return Event("stall", f"stall:{run}", f"Run stalled: {initiative}", f"Run {run} of {initiative}: {reason}")
    return Event(
        "stall",
        f"stall:idle:{action.get('signature') or initiative}",
        f"Idle stall: {initiative}",
        reason,
    )


def _remedy_line(action: Mapping) -> str:
    """The remedy argv as one line; empty when the action carries no command."""
    command = " ".join(str(part) for part in action.get("command") or [])
    return f"Remedy: {command}" if command else ""


def _subject(action: Mapping) -> str:
    """What the hold is on: the initiative, else the task, run or host the action names, else 'unknown'."""
    return next((str(v) for k in ("initiative", "task_id", "run", "host") if (v := action.get(k))), "unknown")


def _needs_chair(action: Mapping) -> Event:
    # The push body is cut to 200 characters and a reason runs to 600, so the remedy goes before the reason.
    cause = str(action.get("cause") or "")
    subject = _subject(action)
    lines = [f"{subject} needs you: {cause}.", _remedy_line(action), str(action.get("reason") or "")]
    return Event(
        "needs_chair", f"needs_chair:{subject}:{cause}", f"Needs you: {subject}", "\n".join(x for x in lines if x)
    )


def _event(action: Mapping) -> Event:
    cause = action.get("cause")
    if cause == "login_lapsed":
        return _host_login(action)
    if cause in ("stalled", "idle_stall"):
        return _stall(action)
    return _needs_chair(action)


# Holds the loop clears by itself on a later tick: a land between fetch and record (no_run), a launch deferred behind a
# running initiative that shares a file (steer_deferred), and a work file the export left alone (held_file).
SELF_CLEARING_CAUSES = frozenset({"no_run", "steer_deferred", "held_file"})


def _is_dependency_wait(action: Mapping) -> bool:
    """A task waiting on another task of its own initiative, or a hold the loop clears by itself, never pushes."""
    cause = action.get("cause")
    return isinstance(cause, str) and (cause.startswith("waiting on ") or cause in SELF_CLEARING_CAUSES)


QUEUE_EMPTY = Event(
    "needs_chair",
    "queue_empty",
    "Queue empty",
    "The lanes are idle: no ready task, intake, approved task or live run. File the next intake.",
)


def _queue_is_empty(facts: Mapping, events: Sequence[Event]) -> bool:
    """True only when every fact is present and empty: facts["dispatch"] holds the local live_runs and a hosts list; approved may be absent."""
    initiatives, intake, dispatch = facts.get("initiatives"), facts.get("intake"), facts.get("dispatch")
    if initiatives is None or intake is None or not isinstance(dispatch, Mapping) or "live_runs" not in dispatch:
        return False
    hosts = dispatch.get("hosts")
    return (
        not events
        and not any(i.get("ready_tasks") for i in initiatives)
        and not intake
        and not facts.get("approved")
        and dispatch["live_runs"] == 0
        and hosts is not None
        and all(h.get("live_runs") == 0 for h in hosts)
    )


def events_from_tick(facts: Mapping, actions: Sequence[Mapping]) -> list[Event]:
    """Events in first-seen key order, one per key, the first action that carries a remedy winning; queue_empty comes last, when facts show nothing left to run."""
    chosen: dict[str, tuple[Mapping, Event]] = {}
    for a in (a for a in actions if a.get("kind") == "needs_chair" and not _is_dependency_wait(a)):
        e = _event(a)
        held = chosen.get(e.key)
        if held is None or (_remedy_line(a) and not _remedy_line(held[0])):
            chosen[e.key] = (a, e)
    built = [e for _, e in chosen.values()]
    return [*built, QUEUE_EMPTY] if _queue_is_empty(facts, built) else built
