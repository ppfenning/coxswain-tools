from dataclasses import replace
from datetime import timedelta

from agent_tools.chair_facts import gather_facts, pull_age_s
from tests.test_chair_facts import NOW, _deps


def _row(minutes_ago: int, kind: str) -> dict:
    return {"ts": (NOW - timedelta(minutes=minutes_ago)).isoformat(), "kind": kind}


def test_the_newest_pull_wins_whatever_its_list_position():
    rows = [_row(60, "pull"), _row(10, "pull"), _row(1, "land"), _row(30, "pull")]
    assert pull_age_s(rows, NOW) == 600.0


def test_no_pull_rows_give_none():
    assert pull_age_s([_row(1, "land")], NOW) is None
    assert pull_age_s([], NOW) is None


def test_the_built_facts_carry_the_key():
    facts = gather_facts(replace(_deps(), actions=lambda: (_row(5, "pull"),)), NOW)
    assert facts["pull_age_s"] == 300.0
