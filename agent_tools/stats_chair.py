"""Pure chair cost: one Claude session's assistant usage, priced with catalog prices."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping

__all__ = ["chair_cost"]

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
