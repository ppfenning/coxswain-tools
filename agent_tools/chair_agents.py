"""Pure rows for the chair's background agents, derived from their transcripts."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from agent_tools.stats_chair import chair_cost

_RUNNING_WINDOW = timedelta(seconds=600)
_LABEL_MAX = 120
_TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def _entry(line: str) -> dict:
    try:
        entry = json.loads(line)
    except ValueError:
        return {}
    return entry if isinstance(entry, dict) else {}


def _when(text: object) -> datetime | None:
    """Parse a transcript timestamp; a zone-less one is read as UTC, an unparsable one is None."""
    if not isinstance(text, str):
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _utc_text(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _label(meta: dict) -> str:
    description = str(meta.get("description") or "")
    lines = description.splitlines()
    return (lines[0] if lines else "")[:_LABEL_MAX]


def _message(entry: dict) -> dict:
    message = entry.get("message")
    return message if isinstance(message, dict) else {}


def _usage_key(line: str, entry: dict) -> str:
    """Message id, else requestId, else the line itself: the key `chair_cost` dedupes on."""
    for candidate in (_message(entry).get("id"), entry.get("requestId")):
        if isinstance(candidate, str) and candidate:
            return candidate
    return line


def _tokens(assistants: list[tuple[str, dict]]) -> int:
    """Tokens with each message counted once, from its first usage block as `chair_cost` does."""
    usages = [
        (_usage_key(line, e), _message(e)["usage"])
        for line, e in assistants
        if isinstance(_message(e).get("usage"), dict)
    ]
    return sum(int(usage.get(key) or 0) for usage in dict(reversed(usages)).values() for key in _TOKEN_KEYS)


def _state(assistants: list[tuple[str, dict]], last: datetime, now: datetime) -> str:
    if assistants and _message(assistants[-1][1]).get("stop_reason") == "end_turn":
        return "done"
    return "running" if now - last <= _RUNNING_WINDOW else "failed"


def _row(agent: dict, prices: dict, now: datetime) -> dict | None:
    lines = agent["lines"]
    parsed = [(line, _entry(line)) for line in lines]
    moments = [m for m in (_when(e.get("timestamp")) for _, e in parsed) if m is not None]
    if not moments:
        return None
    first, last = moments[0], moments[-1]
    assistants = [(line, e) for line, e in parsed if e.get("type") == "assistant"]
    state = _state(assistants, last, now)
    return {
        "label": _label(agent.get("meta") or {}),
        "agent_id": agent["agent_id"],
        "model": _message(assistants[0][1]).get("model") if assistants else None,
        "state": state,
        "started_at": _utc_text(first),
        "finished_at": None if state == "running" else _utc_text(last),
        "tokens": _tokens(assistants),
        "cost_usd": round(chair_cost(lines, prices), 4),
    }


def agent_rows(agents: list[dict], prices: dict, now: datetime, limit: int = 12) -> list[dict]:
    """At most `limit` rows, newest `started_at` first; an agent with no parsable timestamp is dropped."""
    rows = [row for row in (_row(agent, prices, now) for agent in agents) if row is not None]
    return sorted(rows, key=lambda row: row["started_at"], reverse=True)[:limit]
