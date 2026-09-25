import json

from agent_tools.stats_tiers import (
    ModelSummary,
    Recommendation,
    TierRow,
    recommend,
    render_lines,
    savings,
    summarise,
    to_json,
)


def row(
    role: str, model: str, task: str, cost: float = 1.0, turns: int = 1, attempt: int = 1, landed: bool = True
) -> TierRow:
    return TierRow(role, model, task, cost, turns, attempt, landed)


def tasks(role: str, model: str, landed: int, total: int, cost: float = 1.0) -> list[TierRow]:
    return [row(role, model, f"{model}-{i}", cost=cost, landed=i < landed) for i in range(total)]


def only(rows: list[TierRow]) -> ModelSummary:
    (s,) = summarise(rows)
    return s


def recs(rows: list[TierRow], min_samples: int) -> tuple[Recommendation, ...]:
    return recommend(summarise(rows), min_samples)


def test_cost_per_call_is_total_cost_over_calls():
    assert only([row("build", "m", "a", cost=1.0), row("build", "m", "b", cost=2.0)]).cost_per_call == 1.5


def test_average_turns_is_the_mean_over_calls():
    assert only([row("build", "m", "a", turns=2), row("build", "m", "b", turns=5)]).avg_turns == 3.5


def test_landed_rate_counts_distinct_tasks_not_calls():
    rows = [row("build", "m", "a"), row("build", "m", "a", attempt=2), row("build", "m", "b", landed=False)]
    s = only(rows)
    assert (s.calls, s.tasks, s.landed_rate) == (3, 2, 0.5)


def test_first_try_rate_is_set_only_for_build():
    rows = [
        row("build", "m", "a"),
        row("build", "m", "b"),
        row("build", "m", "b", attempt=2),
        row("build", "m", "c", landed=False),
    ]
    s = only(rows)
    assert (s.landed_rate, s.first_try_rate) == (2 / 3, 1 / 3)
    assert only([row("review", "m", "a")]).first_try_rate is None
    handoff = summarise([row("build", "a", "t"), row("build", "b", "t", attempt=2)])
    assert [(s.model, s.first_try_rate) for s in handoff] == [("a", 0.0), ("b", 0.0)]


def test_cost_per_landed_is_total_cost_over_landed_tasks_and_none_at_zero():
    rows = [row("build", "m", "a", cost=1.0), row("build", "m", "b", cost=3.0, landed=False)]
    assert only(rows).cost_per_landed == 4.0
    assert only([row("build", "m", "a", landed=False)]).cost_per_landed is None


def test_eligibility_cuts_at_min_samples():
    rows = (
        tasks("plan", "cheap", 3, 3, cost=0.1)
        + tasks("plan", "mid", 3, 3, cost=0.5)
        + tasks("plan", "thin", 2, 2, cost=0.01)
    )
    assert recs(rows, 3)[0].pick == "cheap"
    assert recs(rows, 2)[0].pick == "thin"


def test_a_landed_rate_exactly_tolerance_below_best_can_still_be_picked():
    near = tasks("plan", "best", 4, 5, cost=1.0) + tasks("plan", "near", 3, 4, cost=0.5)
    assert recs(near, 4)[0].pick == "near"
    over = tasks("plan", "best", 16, 20, cost=1.0) + tasks("plan", "over", 11, 15, cost=0.5)
    assert recs(over, 4)[0].pick == "best"


def test_pick_is_cheapest_then_higher_landed_rate_then_model_name():
    cheapest = tasks("plan", "a", 4, 4, cost=2.0) + tasks("plan", "b", 4, 4, cost=1.0)
    assert recs(cheapest, 4)[0].pick == "b"
    by_rate = tasks("plan", "a", 39, 40, cost=1.0) + tasks("plan", "b", 4, 4, cost=1.0)
    assert recs(by_rate, 4)[0].pick == "b"
    by_name = tasks("plan", "z", 4, 4, cost=1.0) + tasks("plan", "y", 4, 4, cost=1.0)
    assert recs(by_name, 4)[0].pick == "y"


def test_fewer_than_two_eligible_models_is_not_enough_evidence_with_every_count():
    rows = tasks("plan", "a", 3, 3) + tasks("plan", "b", 1, 2)
    assert recs(rows, 3)[0] == Recommendation("plan", None, None, None, False, (("a", 3), ("b", 2)))


def test_savings_is_signed_calls_times_the_cost_per_call_gap():
    rows = tasks("plan", "dear", 4, 4, cost=3.0) + tasks("plan", "cheap", 4, 4, cost=1.0)
    assert savings(rows, recs(rows, 4)) == {"plan": 8.0}
    reliable_but_dear = tasks("plan", "a", 4, 4, cost=3.0) + tasks("plan", "b", 2, 4, cost=1.0)
    assert savings(reliable_but_dear, recs(reliable_but_dear, 4)) == {"plan": -8.0}


def test_savings_is_zero_when_there_is_no_evidence():
    rows = tasks("plan", "a", 1, 1, cost=5.0)
    assert savings(rows, recs(rows, 3)) == {"plan": 0.0}


def fixture() -> tuple[tuple[ModelSummary, ...], tuple[Recommendation, ...], dict[str, float]]:
    rows = tasks("build", "big", 2, 2, cost=2.0) + tasks("build", "small", 2, 2, cost=1.0) + tasks("plan", "big", 1, 1)
    found = recs(rows, 2)
    return summarise(rows), found, savings(rows, found)


def test_render_lines_lists_models_verdict_and_savings():
    assert render_lines(*fixture()) == [
        "build",
        "  big: calls 2, tasks 2, $2.0000/call, turns 1.0, landed 100%, first-try 100%, $2.0000/landed",
        "  small: calls 2, tasks 2, $1.0000/call, turns 1.0, landed 100%, first-try 100%, $1.0000/landed",
        "  pick: small (best landed rate: big)",
        "  savings: +2.0000 USD",
        "plan",
        "  big: calls 1, tasks 1, $1.0000/call, turns 1.0, landed 100%, first-try -, $1.0000/landed",
        "  not enough evidence (big: 1 tasks)",
        "  savings: +0.0000 USD",
    ]


def test_to_json_is_a_plain_json_dict():
    out = to_json(*fixture())
    assert json.loads(json.dumps(out)) == out
    assert out["recommendations"][1] == {
        "role": "plan",
        "pick": None,
        "best": None,
        "pick_cost_per_call": None,
        "enough_evidence": False,
        "task_counts": [{"model": "big", "tasks": 1}],
    }
    assert out["savings"] == {"build": 2.0, "plan": 0.0}
