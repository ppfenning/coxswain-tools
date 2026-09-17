"""Pure core for tier selection. No file reads, no subprocess, no clock: the
CLI edge gathers stats_window and policy and hands them here.

Field and reason-string names below follow docs/design/router-steward.md
verbatim, not the ticket's looser paraphrase. router-steward.md §1: "`policy`
carries four fields: `default_tier`, `min_n`, `deviation_floor`,
`challenger_n`." — no fifth field for an ineligible-role list. §1 also fixes
the reason strings: "`n < min_n` returns `(default_tier, "insufficient_n")`"
and "`landed_rate < deviation_floor` returns one tier above `default_tier`
with reason `"landed_rate_low"`. Otherwise it returns `(default_tier,
"default")`." §2 fixes the ineligible roles as a fact, not a policy input:
"`build` and `arbitrate` are never eligible" — so they are a module constant
here, matching the doc's four-field `policy` shape exactly.

router-steward.md §1 ties `default_tier` to `agent_tools/cli.py`'s
`_bounds_ceiling_for`, which resolves it through a profile's `tier_overrides`
and `defaults` chain, and that chain's rungs are `agent_tools/route.py`'s
`_TIER_LADDER = ("cheap", "standard", "deep")` (route.py:468). This module
reuses that ladder rather than inventing a fourth vocabulary.
"""

from __future__ import annotations

from collections.abc import Mapping

from agent_tools.route import _TIER_LADDER

__all__ = ["select_tier"]

# router-steward.md §2: "`build` and `arbitrate` are never eligible."
_CHALLENGER_INELIGIBLE = frozenset({"build", "arbitrate"})


def _shift(tier: str, delta: int) -> str:
    index = _TIER_LADDER.index(tier) + delta
    return _TIER_LADDER[max(0, min(len(_TIER_LADDER) - 1, index))]


def _is_challenger(role: str, stats_window: Mapping, policy: Mapping) -> bool:
    """router-steward.md §2: "n > 0 and n % policy["challenger_n"] == 0"."""
    n = stats_window["n"]
    return role not in _CHALLENGER_INELIGIBLE and n > 0 and n % policy["challenger_n"] == 0


def select_tier(role: str, stats_window: Mapping, policy: Mapping) -> tuple[str, str]:
    """router-steward.md §1 order: challenger check, then min_n, then
    deviation_floor, else default_tier — see module docstring for the
    quoted reason strings this returns."""
    default_tier = policy["default_tier"]
    if _is_challenger(role, stats_window, policy):
        return _shift(default_tier, -1), "challenger"
    if stats_window["n"] < policy["min_n"]:
        return default_tier, "insufficient_n"
    if stats_window["landed_rate"] < policy["deviation_floor"]:
        return _shift(default_tier, 1), "landed_rate_low"
    return default_tier, "default"
