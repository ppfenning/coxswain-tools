import copy

from agent_tools.chair_spend_nodes import cost_per_done_task, group_spend_nodes

ROWS = [
    {
        "role": "build",
        "tier": "mid",
        "model_alias": "sonnet",
        "turns": 10,
        "input_total": 1000,
        "output_tokens": 200,
        "cost_usd": 0.5,
    },
    {
        "role": "plan",
        "tier": "top",
        "model_alias": "opus",
        "turns": 3,
        "input_total": 400,
        "output_tokens": 100,
        "cost_usd": 1.5,
    },
    {
        "role": "build",
        "tier": "mid",
        "model_alias": "sonnet",
        "turns": 5,
        "input_total": 500,
        "output_tokens": 50,
        "cost_usd": 0.25,
    },
]


def test_three_rows_group_and_sort_by_cost_descending() -> None:
    # The fixture's five_hour window carries no by_node entries, so the expectation is written by hand.
    assert group_spend_nodes(ROWS) == [
        {"role": "plan", "tier": "top", "model": "opus", "calls": 1, "turns": 3, "tokens": 500, "cost_usd": 1.5},
        {"role": "build", "tier": "mid", "model": "sonnet", "calls": 2, "turns": 15, "tokens": 1750, "cost_usd": 0.75},
    ]


def test_empty_rows_give_empty_list() -> None:
    assert group_spend_nodes([]) == []


def test_equal_cost_ties_break_on_role_tier_model() -> None:
    rows = [
        {
            "role": "b",
            "tier": "x",
            "model_alias": "m",
            "turns": 1,
            "input_total": 1,
            "output_tokens": 1,
            "cost_usd": 1.0,
        },
        {
            "role": "a",
            "tier": "y",
            "model_alias": "m",
            "turns": 1,
            "input_total": 1,
            "output_tokens": 1,
            "cost_usd": 1.0,
        },
        {
            "role": "a",
            "tier": "x",
            "model_alias": "n",
            "turns": 1,
            "input_total": 1,
            "output_tokens": 1,
            "cost_usd": 1.0,
        },
        {
            "role": "a",
            "tier": "x",
            "model_alias": "m",
            "turns": 1,
            "input_total": 1,
            "output_tokens": 1,
            "cost_usd": 1.0,
        },
    ]
    assert [(g["role"], g["tier"], g["model"]) for g in group_spend_nodes(rows)] == [
        ("a", "x", "m"),
        ("a", "x", "n"),
        ("a", "y", "m"),
        ("b", "x", "m"),
    ]


def test_two_done_tasks_divide_cost() -> None:
    assert cost_per_done_task(4.0, 2) == 2.0


def test_zero_done_tasks_give_zero() -> None:
    assert cost_per_done_task(4.0, 0) == 0.0


def test_input_rows_are_unchanged() -> None:
    before = copy.deepcopy(ROWS)
    group_spend_nodes(ROWS)
    assert before == ROWS
