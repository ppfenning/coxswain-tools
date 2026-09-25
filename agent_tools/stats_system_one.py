"""Shadow-to-on graduation report for the system-one fast path.

Pure core over already-parsed `runs/<run>.usage.json` dicts: the CLI edge reads
the files and writes the proposal. Nothing here reads a clock or a profile.
"""

from __future__ import annotations

import datetime
import itertools
from dataclasses import dataclass
from typing import Any

# These three bars mirror graphs' MIN_ROWS and MIN_AGREEMENT plus the ticket's costly-direction cap.
# The build cannot read graphs, so keep them in step by hand.
MIN_ROWS = 100
MIN_AGREEMENT = 0.95
MAX_COSTLY_RATE = 0.01

# role -> the fast-path answer whose disagreement with the LLM is the expensive one.
COSTLY_ANSWER = {"handoff": "yes", "review_charter": "approve"}


@dataclass(frozen=True)
class Row:
    role: str
    ts: datetime.datetime
    model_id: str | None
    claude_code_version: str | None
    mode: str | None
    answer: str | None
    confidence: float | None
    threshold: float | None
    agreed: bool | None


@dataclass(frozen=True)
class Summary:
    role: str
    model_id: str | None
    claude_code_version: str | None
    first: datetime.datetime
    last: datetime.datetime
    shadow: int
    covered: int
    agreed: int
    costly: int
    other: int

    @property
    def days(self) -> float:
        """Window length in days, never under one so a short window is not extrapolated."""
        return max(1.0, (self.last - self.first).total_seconds() / 86400)

    @property
    def agreement(self) -> float | None:
        return self.agreed / self.covered if self.covered else None

    @property
    def costly_rate(self) -> float | None:
        return self.costly / self.covered if self.covered else None

    @property
    def saved_per_week(self) -> float:
        """LLM calls skipped per week: covered rows over the window's days, times 7."""
        return self.covered / self.days * 7


def _row(call: dict[str, Any]) -> Row | None:
    decision = call.get("decision")
    if not isinstance(decision, dict):
        return None
    try:
        ts = datetime.datetime.fromisoformat(call["ts"])
    except (KeyError, TypeError, ValueError):
        return None
    return Row(
        role=decision.get("role") or call.get("role") or "",
        ts=ts,
        model_id=decision.get("model_id"),
        claude_code_version=decision.get("claude_code_version"),
        mode=decision.get("system_one_mode"),
        answer=decision.get("system_one_answer"),
        confidence=decision.get("system_one_confidence"),
        threshold=decision.get("system_one_threshold"),
        agreed=decision.get("system_one_agreed"),
    )


def rows_from_usage(usages: list[dict[str, Any]]) -> list[Row]:
    """Every call that carries a decision. A call with no `decision` key, or a null one, is skipped."""
    calls = (c for u in usages for c in (u.get("calls") or []) if isinstance(c, dict))
    return [r for c in calls if (r := _row(c)) is not None and r.role]


def _key(r: Row) -> tuple[str | None, str | None]:
    return (r.model_id, r.claude_code_version)


def window(rows: list[Row], role: str, since: str | None) -> list[Row]:
    """The role's decision rows on or after `since`, cut to the trailing run that shares one model_id and claude_code_version."""
    mine = sorted(
        (r for r in rows if r.role == role and (since is None or r.ts.date().isoformat() >= since)),
        key=lambda r: r.ts,
    )
    return list(reversed(list(itertools.takewhile(lambda r: _key(r) == _key(mine[-1]), reversed(mine))))) if mine else []


def is_covered(r: Row) -> bool:
    return r.confidence is not None and r.threshold is not None and r.confidence >= r.threshold


def is_costly(r: Row) -> bool:
    """The fast path said the good thing and the LLM disagreed."""
    return r.agreed is False and r.answer is not None and r.answer == COSTLY_ANSWER.get(r.role)


def summarize(role: str, rows: list[Row]) -> Summary | None:
    """None when the window holds no shadow rows."""
    shadow = [r for r in rows if r.mode == "shadow"]
    covered = [r for r in shadow if is_covered(r)]
    costly = [r for r in covered if is_costly(r)]
    disagreed = [r for r in covered if r.agreed is False]
    return (
        Summary(
            role=role,
            model_id=shadow[-1].model_id,
            claude_code_version=shadow[-1].claude_code_version,
            first=shadow[0].ts,
            last=shadow[-1].ts,
            shadow=len(shadow),
            covered=len(covered),
            agreed=sum(1 for r in covered if r.agreed is True),
            costly=len(costly),
            other=len(disagreed) - len(costly),
        )
        if shadow
        else None
    )


def summaries(rows: list[Row], since: str | None, role: str | None) -> list[Summary]:
    roles = sorted({r.role for r in rows} if role is None else {role})
    return [s for ro in roles if (s := summarize(ro, window(rows, ro, since))) is not None]


def verdict(s: Summary) -> tuple[str, list[str]]:
    """("READY", []) or ("NOT YET", the unmet bars, each with its number)."""
    unmet = [
        *([f"rows {s.shadow} < {MIN_ROWS}"] if s.shadow < MIN_ROWS else []),
        *(["no covered rows"] if s.covered == 0 else []),
        *([f"agreement {s.agreement:.3f} < {MIN_AGREEMENT}"] if s.agreement is not None and s.agreement < MIN_AGREEMENT else []),
        *([f"costly-direction rate {s.costly_rate:.3f} > {MAX_COSTLY_RATE}"] if s.costly_rate is not None and s.costly_rate > MAX_COSTLY_RATE else []),
    ]
    return ("NOT YET", unmet) if unmet else ("READY", [])


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def render_role(s: Summary) -> str:
    label, unmet = verdict(s)
    return "\n".join([
        f"{s.role}: {label}" + (f" ({'; '.join(unmet)})" if unmet else ""),
        f"  window: {s.first.date().isoformat()} to {s.last.date().isoformat()}, model {s.model_id}, claude_code {s.claude_code_version}",
        f"  shadow rows {s.shadow}, covered {s.covered} ({_pct(s.covered / s.shadow)})",
        f"  agreement among covered {_pct(s.agreement)} ({s.agreed} of {s.covered})",
        f"  disagreements: costly direction {s.costly} ({_pct(s.costly_rate)} of covered), other {s.other}",
    ])


def render_report(ss: list[Summary]) -> str:
    return "\n\n".join(render_role(s) for s in ss) if ss else "no shadow rows in any usage file"


def to_json(ss: list[Summary]) -> dict[str, Any]:
    def one(s: Summary) -> dict[str, Any]:
        label, unmet = verdict(s)
        return {
            "role": s.role, "model_id": s.model_id, "claude_code_version": s.claude_code_version,
            "first": s.first.isoformat(), "last": s.last.isoformat(), "days": s.days,
            "shadow": s.shadow, "covered": s.covered, "agreed": s.agreed, "agreement": s.agreement,
            "costly": s.costly, "costly_rate": s.costly_rate, "other": s.other,
            "saved_per_week": s.saved_per_week, "verdict": label, "unmet": unmet,
        }

    return {"roles": [one(s) for s in ss]}


def render_proposal(s: Summary, date: str) -> str:
    """The graduation proposal for the maintainer. It states a cost cut with its quality cost and edits nothing."""
    label, unmet = verdict(s)
    return f"""# System one graduation: {s.role}, {date}

Verdict: {label}{f" ({'; '.join(unmet)})" if unmet else ""}

Proposal only. Nothing was edited. The maintainer approves and makes the change.

## Numbers

Window {s.first.date().isoformat()} to {s.last.date().isoformat()} ({s.days:.1f} days), model {s.model_id}, claude_code {s.claude_code_version}. A change of either restarts the count.

- Shadow rows: {s.shadow} (bar {MIN_ROWS})
- Covered rows: {s.covered} ({_pct(s.covered / s.shadow)} of shadow)
- Agreement among covered: {_pct(s.agreement)}, {s.agreed} of {s.covered} (bar {MIN_AGREEMENT * 100:.0f}%)
- Costly-direction disagreements: {s.costly} ({_pct(s.costly_rate)} of covered, bar {MAX_COSTLY_RATE * 100:.0f}%)
- Other disagreements: {s.other}

## The change

In the provider profile the harness runs with (for Claude Code, `providers/claude-code.yaml` in coxswain-cartridges), change the role's mode under `system_one.roles` from shadow to on. Keep its threshold.

    system_one.roles.{s.role}.mode:  shadow  ->  on

## What it saves

About {s.saved_per_week:.1f} LLM calls per week for `{s.role}` would be skipped: {s.covered} covered rows over {s.days:.1f} days, times 7.

## What it risks

The costly direction is the fast path answering `{COSTLY_ANSWER.get(s.role, "n/a")}` where the LLM node would not have. In this window that happened {s.costly} times in {s.covered} covered rows ({_pct(s.costly_rate)}). With the mode on, those calls would pass without the LLM's check.
"""
