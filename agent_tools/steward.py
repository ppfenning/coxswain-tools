"""Pure steward core: ceiling-change evidence bar and proposal rendering.

Implements the ceiling-raise/lower half of `cox steward propose` per
docs/design/router-steward.md §4-5. Rows are shaped like
`stats_query.bounds_report`'s per-(role, model) output — role, model,
ceiling, censored, n, strict, moderate, liberal — extended with the
challenger/floor landed rates and window span the evidence bar needs:
n_challenger, landed_rate_challenger, landed_rate_floor, window_days.
Building those rows from stats.db is the caller's job; this module opens
no file and no db connection.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _clears_bar(row: Mapping[str, Any], policy: Mapping[str, Any]) -> bool:
    delta = row["landed_rate_challenger"] - row["landed_rate_floor"]
    return (
        row["n_challenger"] >= policy["min_n"]
        and row["window_days"] <= policy["window_days_cap"]
        and abs(delta) >= policy["landed_rate_delta_floor"]
    )


def _candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    delta = row["landed_rate_challenger"] - row["landed_rate_floor"]
    direction = "raise" if delta > 0 else "lower"
    proposed_ceiling = row["liberal"] if delta > 0 else row["moderate"]
    return {
        "role": row["role"],
        "model": row["model"],
        "direction": direction,
        "current_ceiling": row["ceiling"],
        "proposed_ceiling": proposed_ceiling,
        "n": row["n_challenger"],
        "landed_rate_challenger": row["landed_rate_challenger"],
        "landed_rate_floor": row["landed_rate_floor"],
        "window_days": row["window_days"],
    }


def ceiling_candidates(rows: list[Mapping[str, Any]], policy: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Rows clearing router-steward.md §5's bar, each carrying the numbers a reviewer judges it by."""
    return [_candidate(row) for row in rows if _clears_bar(row, policy)]


def render_proposal(candidate: Mapping[str, Any]) -> str:
    """Proposal body for a candidate that already cleared the evidence bar."""
    verb = candidate["direction"]
    headline = (
        f"{verb.capitalize()} {candidate['role']}'s ceiling for {candidate['model']} "
        f"from {candidate['current_ceiling']} to {candidate['proposed_ceiling']}."
    )
    header = "| role | model | n | landed_rate_challenger | landed_rate_floor | window_days |"
    divider = "| --- | --- | --- | --- | --- | --- |"
    data_row = (
        f"| {candidate['role']} | {candidate['model']} | {candidate['n']} | "
        f"{candidate['landed_rate_challenger']} | {candidate['landed_rate_floor']} | "
        f"{candidate['window_days']} |"
    )
    return "\n".join([headline, "", header, divider, data_row])
