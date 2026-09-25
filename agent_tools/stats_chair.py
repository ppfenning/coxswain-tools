"""Pure chair cost: one Claude session's assistant usage, priced with catalog prices."""

from __future__ import annotations

import json
import textwrap
from collections.abc import Iterable, Mapping
from typing import NamedTuple

import yaml

__all__ = [
    "CAUSES",
    "catalog_prices",
    "chair_cost",
    "chair_report",
    "frontmatter_item",
    "hand_finished",
    "lines_since",
    "quarantine_cost_by_cause",
    "render_chair",
    "session_ids",
    "set_attempt_cause",
]

CAUSES = ("ticket", "code", "harness", "unknown")

_PER_MILLION = 1_000_000

# usage field in the transcript -> price key in the catalog
_USAGE_TO_PRICE = (
    ("input_tokens", "input"),
    ("output_tokens", "output"),
    ("cache_creation_input_tokens", "cache_write"),
    ("cache_read_input_tokens", "cache_read"),
)


def _parse(line: str) -> dict | None:
    try:
        parsed = json.loads(line)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _line_cost(line: str, prices: Mapping[str, Mapping[str, float]]) -> float:
    """0.0 for a line that is not an assistant usage block or names a model with no price."""
    entry = _parse(line)
    message = entry.get("message") if entry is not None and entry.get("type") == "assistant" else None
    usage = message.get("usage") if isinstance(message, dict) else None
    price = prices.get(message.get("model")) if isinstance(usage, dict) else None
    if not isinstance(usage, dict) or price is None:
        return 0.0
    return sum((usage.get(field) or 0) * price.get(key, 0.0) for field, key in _USAGE_TO_PRICE) / _PER_MILLION


def chair_cost(transcript_lines: Iterable[str], prices: Mapping[str, Mapping[str, float]]) -> float:
    """Dollars for the assistant usage blocks in `transcript_lines`; `prices` is $ per million tokens by model."""
    return sum(_line_cost(line, prices) for line in transcript_lines)


def _attempt_causes(items: Iterable[Mapping]) -> dict[tuple[str, str], str | None]:
    """(item id, run) -> the cause on that item's newest attempt for that run."""
    return {
        (str(item.get("id")), str(attempt.get("run"))): attempt.get("cause")
        for item in items
        for attempt in (item.get("attempts") or [])
        if isinstance(attempt, Mapping)
    }


def quarantine_cost_by_cause(items: Iterable[Mapping], usage_calls: Iterable[Mapping]) -> dict[str, float]:
    """$ per cause; each call carries the `run` of the usage file it came from, set by the caller."""
    cause_of = _attempt_causes(items)
    caused = [
        (cause_of.get((str(c["task_id"]), str(c.get("run")))), c.get("cost_usd") or 0.0)
        for c in usage_calls
        if c.get("task_id") is not None
    ]
    return {cause: sum(cost for found, cost in caused if found == cause) for cause in CAUSES}


def catalog_prices(catalog: object) -> dict[str, dict[str, float]]:
    """model -> price mapping from a parsed catalog; entries without a `price` mapping are dropped."""
    models = catalog.get("models", catalog) if isinstance(catalog, Mapping) else {}
    entries = models.items() if isinstance(models, Mapping) else []
    return {
        str(name): dict(entry["price"])
        for name, entry in entries
        if isinstance(entry, Mapping) and isinstance(entry.get("price"), Mapping)
    }


def lines_since(lines: Iterable[str], since: str | None) -> list[str]:
    """`lines` whose timestamp date is on or after `since` (YYYY-MM-DD); every line when `since` is None."""
    parsed = [(line, _parse(line)) for line in lines]
    return [
        line
        for line, entry in parsed
        if since is None or (entry is not None and str(entry.get("timestamp") or "")[:10] >= since)
    ]


def session_ids(chair_record: Mapping | None, extra: Iterable[str]) -> list[str]:
    """Claude session ids from chair.json (`claude_session`, then `history` entries) and `extra`, first seen first, no repeats."""
    record = chair_record or {}
    history = record.get("history") if isinstance(record.get("history"), list) else []
    named = [
        record.get("claude_session"),
        *(h.get("claude_session") if isinstance(h, Mapping) else h for h in history),
        *extra,
    ]
    return list(dict.fromkeys(str(n) for n in named if n))


def hand_finished(landed: Iterable[tuple[str, str]], land_rows: Iterable[Mapping]) -> list[tuple[str, str]]:
    """The (run, task) pairs in `landed` with no land row that reached `merge` with exit 0."""
    merged = {
        (str(row.get("run")), str(row.get("task")))
        for row in land_rows
        if row.get("exit") == 0 and "merge" in (row.get("steps_reached") or [])
    }
    return [pair for pair in landed if pair not in merged]


def chair_report(
    since: str | None,
    landed: list[tuple[str, str]],
    usage_calls: list[Mapping],
    land_rows: list[Mapping],
    chair_usd: float | None,
    items: list[Mapping],
) -> dict:
    """One entry per fact. `chair_usd` None means no catalog priced the transcripts."""
    prs = len(landed)
    by_hand = len(hand_finished(landed, land_rows))
    return {
        "window": {"since": since},
        "harness_prs": prs,
        "harness_usd": sum(c.get("cost_usd") or 0.0 for c in usage_calls),
        "chair_usd": chair_usd,
        "chair_usd_per_pr": chair_usd / prs if chair_usd is not None and prs else None,
        "hand_finished": {"n": by_hand, "pct": round(100 * by_hand / prs, 1) if prs else None},
        "quarantine_usd": quarantine_cost_by_cause(items, usage_calls),
    }


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.2f}"


def render_chair(report: Mapping) -> str:
    """The report as a two-column table."""
    hand = report["hand_finished"]
    pct = "n/a" if hand["pct"] is None else f"{hand['pct']}%"
    rows = [
        ("window", f"since {report['window']['since'] or 'the beginning'}"),
        ("harness PRs", str(report["harness_prs"])),
        ("harness $", _money(report["harness_usd"])),
        ("chair $", _money(report["chair_usd"])),
        ("chair $ per harness PR", _money(report["chair_usd_per_pr"])),
        ("hand-finished lands", f"{hand['n']} ({pct})"),
        *((f"quarantine $ {cause}", _money(usd)) for cause, usd in report["quarantine_usd"].items()),
    ]
    width = max(len(name) for name, _ in rows)
    return "\n".join(f"{name.ljust(width)}  {value}" for name, value in rows)


def frontmatter_item(text: str, stem: str) -> dict:
    """A work item's `---` frontmatter as a mapping with `id` defaulting to `stem`; only `id` when it does not parse."""
    close = text.find("\n---", 4) if text.startswith("---\n") else -1
    try:
        parsed = yaml.safe_load(text[4:close]) if close != -1 else None
    except yaml.YAMLError:
        parsed = None
    fields = parsed if isinstance(parsed, dict) else {}
    return {**fields, "id": fields.get("id", stem)}


class _Entry(NamedTuple):
    first: int
    end: int
    indent: str
    flow: bool
    value: object


def _frontmatter_close(lines: list[str]) -> int | None:
    if lines[:1] == ["---\n"]:
        return next((i for i in range(1, len(lines)) if lines[i].rstrip("\n") == "---"), None)
    return None


def _block_end(lines: list[str], start: int, close: int) -> int:
    """The first line after `start` that is a new top-level key, or `close`."""
    return next((i for i in range(start + 1, close) if not (lines[i][:1].isspace() or lines[i].startswith("- "))), close)


def _entry(lines: list[str], first: int, end: int, indent: str) -> _Entry:
    parsed = yaml.safe_load(textwrap.dedent("".join(lines[first:end])))
    value = parsed[0] if isinstance(parsed, list) and len(parsed) == 1 else None
    return _Entry(first, end, indent, lines[first][len(indent) + 2:].startswith("{"), value)


# Hand-rolled on purpose: route.parse_frontmatter reads list items back as strings, and
# fragments.py re-dumps a whole document; editing one entry in place needs its line span.
def _entries(lines: list[str]) -> list[_Entry]:
    """The entries of a block-style frontmatter `attempts:` list; a flow list or no list gives none."""
    close = _frontmatter_close(lines)
    start = next((i for i in range(1, close) if lines[i].rstrip() == "attempts:"), None) if close is not None else None
    end = _block_end(lines, start, close) if start is not None else 0
    items = [i for i in range(start + 1, end) if lines[i].lstrip().startswith("- ")] if start is not None else []
    indent = lines[items[0]][: len(lines[items[0]]) - len(lines[items[0]].lstrip())] if items else ""
    starts = [i for i in items if lines[i].startswith(indent + "- ")]
    return [_entry(lines, a, b, indent) for a, b in zip(starts, [*starts[1:], end])]


def _render(entry: Mapping, indent: str, flow: bool) -> str:
    """One list entry in the style it was read in: a flow mapping on one line, or a block mapping."""
    dumped = yaml.safe_dump([dict(entry)], default_flow_style=flow, sort_keys=False, allow_unicode=True, width=10**6)
    if flow:
        return f"{indent}- {dumped.strip()[1:-1]}\n"
    return "".join(f"{indent}{line}" for line in dumped.splitlines(keepends=True))


def set_attempt_cause(text: str, run: str, cause: str, note: str | None) -> str | None:
    """`text` with only the newest `attempts:` mapping whose `run` is `run` rewritten; None when there is none."""
    lines = text.splitlines(keepends=True)
    try:
        entries = _entries(lines)
    except yaml.YAMLError:
        return None
    matching = [e for e in entries if isinstance(e.value, Mapping) and str(e.value.get("run")) == run]
    if matching:
        target = matching[-1]
        updated = {**target.value, "cause": cause, **({"note": note} if note is not None else {})}
        return "".join(lines[: target.first]) + _render(updated, target.indent, target.flow) + "".join(lines[target.end :])
    return None
