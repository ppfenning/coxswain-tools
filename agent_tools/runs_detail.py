"""The pure model behind `cox runs detail`; `NODE_ORDER` is the graph's node sequence, not a clock, because trace-name events carry no start order."""

from __future__ import annotations

from dataclasses import dataclass

from agent_tools.events import Event

__all__ = ["NODE_ORDER", "Detail", "NodeCall", "detail", "render"]

NODE_ORDER = (
    "plan",
    "scope_epic",
    "build",
    "handoff",
    "review_charter",
    "review_adversary",
    "arbitrate",
    "validate_chunk",
    "validate_phase",
)


@dataclass(frozen=True)
class NodeCall:
    node: str
    attempt: int
    turns: int
    cost_usd: float
    verdict: str = ""


@dataclass(frozen=True)
class Detail:
    run: str
    alive: bool
    status: str
    timeline: tuple[NodeCall, ...]
    objection: str
    quarantine_reason: str
    files_touched: tuple[str, ...]
    changed_lines: int
    last_calls: tuple[str, ...]


def _status(events: list[Event], alive: bool) -> str:
    if any(e.kind == "task_quarantined" for e in events):
        return "quarantined"
    if any(e.kind == "budget_stop" for e in events):
        return "budget"
    if not alive or any(e.kind == "run_exited" for e in events):
        return "exited"
    return "running"

def _order_key(node: str) -> int:
    return NODE_ORDER.index(node) if node in NODE_ORDER else len(NODE_ORDER)


def _timeline(events: list[Event], calls: list[dict]) -> tuple[NodeCall, ...]:
    seen: list[tuple[str, int]] = []
    for e in events:
        if e.kind != "node_started":
            continue
        key = (e.detail["node"], e.detail["attempt"])
        if key not in seen:
            seen.append(key)
    calls_by_key = {(c["node"], c["attempt"]): c for c in calls}
    last_verdict: dict[str, str] = {}
    for e in events:
        if e.kind == "verdict":
            last_verdict[e.detail["node"]] = e.detail["verdict"]
    max_attempt: dict[str, int] = {}
    for node, attempt in seen:
        max_attempt[node] = max(max_attempt.get(node, 0), attempt)
    entries = [
        NodeCall(
            node=node,
            attempt=attempt,
            turns=calls_by_key.get((node, attempt), {}).get("turns", 0),
            cost_usd=calls_by_key.get((node, attempt), {}).get("cost_usd", 0.0),
            verdict=last_verdict.get(node, "") if attempt == max_attempt[node] else "",
        )
        for node, attempt in seen
    ]
    return tuple(sorted(entries, key=lambda nc: (nc.attempt, _order_key(nc.node), nc.node)))


def _first_sentence(text: str) -> str:
    first, _, _ = text.strip().partition(".")
    return first.strip()


def _objection(events: list[Event], record: dict) -> str:
    verdicts = [e for e in events if e.kind == "verdict"]
    if not verdicts or verdicts[-1].detail["verdict"] != "revise":
        return ""
    arbitration = record.get("arbitration")
    reasoning = getattr(arbitration, "reasoning", "") if arbitration is not None else ""
    if reasoning:
        return _first_sentence(reasoning)
    adversary = record.get("adversary") or []
    findings = adversary if isinstance(adversary, list) else [adversary]
    for finding in findings:
        why_wrong = (finding or {}).get("why_wrong") if isinstance(finding, dict) else None
        if why_wrong:
            return _first_sentence(why_wrong)
    return ""


def _quarantine_reason(events: list[Event]) -> str:
    quarantined = [e for e in events if e.kind == "task_quarantined"]
    return quarantined[-1].detail.get("reason", "") if quarantined else ""


def detail(run: str, alive: bool, events: list[Event], calls: list[dict], record: dict, tail: list[str]) -> Detail:
    """Pure: the one Detail a run's events, finished calls, task record and
    trace tail make. `record` may be `{}`; every derived field is then ""
    or empty, never a raise."""
    change_facts = record.get("change_facts") or {}
    return Detail(
        run=run,
        alive=alive,
        status=_status(events, alive),
        timeline=_timeline(events, calls),
        objection=_objection(events, record),
        quarantine_reason=_quarantine_reason(events),
        files_touched=tuple(change_facts.get("files_touched") or ()),
        changed_lines=int(change_facts.get("changed_lines") or 0),
        last_calls=tuple(tail[-3:]),
    )


def _cut(line: str, width: int) -> str:
    return line[: max(width, 0)]


def render(detail: Detail, width: int) -> list[str]:
    lines = [f"run {detail.run} [{detail.status}]"]
    lines += [f"{nc.node}#{nc.attempt}  {nc.turns}  ${nc.cost_usd:.2f}  {nc.verdict}" for nc in detail.timeline]
    if detail.objection:
        lines.append(f"objection: {detail.objection}")
    if detail.quarantine_reason:
        lines.append(f"quarantine: {detail.quarantine_reason}")
    if detail.files_touched:
        lines.append(f"files: {', '.join(detail.files_touched)}")
    if detail.last_calls:
        lines.append(f"last: {', '.join(detail.last_calls)}")
    return [_cut(line, width) for line in lines]
