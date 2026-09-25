import datetime

from agent_tools import stats_system_one as s1

OFF_CALL = {
    "role": "scope_epic", "model": "haiku", "ts": "2026-09-25T02:41:32.306030+00:00", "cost_usd": 0.057401,
    "decision": {
        "role": "scope_epic", "model_id": "claude-haiku-4-5-20251001", "claude_code_version": "2.1.280",
        "system_one_backend": None, "system_one_mode": None, "system_one_answer": None,
        "system_one_confidence": None, "system_one_threshold": None, "system_one_agreed": None,
    },
}


def call(i, *, role="handoff", model="m1", version="v1", answer="yes", conf=0.9, agreed=True, mode="shadow", day=1):
    ts = datetime.datetime(2026, 9, day, tzinfo=datetime.UTC) + datetime.timedelta(minutes=i)
    return {
        "role": role, "ts": ts.isoformat(),
        "decision": {
            "role": role, "model_id": model, "claude_code_version": version, "system_one_mode": mode,
            "system_one_answer": answer, "system_one_confidence": conf, "system_one_threshold": 0.8,
            "system_one_agreed": agreed,
        },
    }


def summary_of(calls, since=None, role="handoff"):
    (s,) = s1.summaries(s1.rows_from_usage([{"calls": calls}]), since, role)
    return s


def test_a_call_with_no_decision_or_a_null_one_is_skipped_and_an_off_row_is_no_shadow_row():
    rows = s1.rows_from_usage([{"calls": [{"role": "build", "ts": "2026-09-01T00:00:00+00:00"}, {"ts": "x", "decision": None}, OFF_CALL]}])
    assert [r.mode for r in rows] == [None]
    assert s1.summaries(rows, None, None) == []


def test_ready_at_exactly_the_bars():
    calls = [call(i, agreed=i >= 5) for i in range(100)]
    calls[99] = call(99, answer="no", agreed=False)
    s = summary_of(calls)
    assert (s.shadow, s.covered, s.agreed, s.costly) == (100, 100, 94, 5)
    assert s1.verdict(s)[0] == "NOT YET"
    ok = [call(i, answer="no", agreed=i >= 5) for i in range(100)]
    assert s1.verdict(summary_of(ok)) == ("READY", [])


def test_not_yet_names_each_unmet_bar():
    s = summary_of([call(i, agreed=i > 10) for i in range(50)])
    label, unmet = s1.verdict(s)
    assert label == "NOT YET"
    assert unmet == ["rows 50 < 100", "agreement 0.780 < 0.95", "costly-direction rate 0.220 > 0.01"]


def test_the_costly_rate_bar_is_one_percent_of_covered():
    one = [call(0, agreed=False), *[call(i) for i in range(1, 100)]]
    assert s1.verdict(summary_of(one)) == ("READY", [])
    two = [call(0, agreed=False), call(1, agreed=False), *[call(i) for i in range(2, 100)]]
    assert s1.verdict(summary_of(two))[0] == "NOT YET"


def test_uncovered_rows_count_as_shadow_but_not_toward_agreement():
    s = summary_of([call(0, conf=0.5, agreed=False), call(1)])
    assert (s.shadow, s.covered, s.agreed, s.costly) == (2, 1, 1, 0)


def test_no_covered_rows_is_not_yet_and_does_not_divide_by_zero():
    s = summary_of([call(i, conf=None) for i in range(120)])
    assert s1.verdict(s) == ("NOT YET", ["no covered rows"])


def test_costly_direction_per_role():
    cases = [
        ("handoff", "yes", False, True), ("handoff", "no", False, False), ("handoff", "yes", None, False),
        ("review_charter", "approve", False, True), ("review_charter", "revise", False, False),
        ("build", "yes", False, False),
    ]
    rows = s1.rows_from_usage([{"calls": [call(i, role=r, answer=a, agreed=g) for i, (r, a, g, _) in enumerate(cases)]}])
    assert [s1.is_costly(r) for r in rows] == [want for *_, want in cases]


def test_a_model_change_restarts_the_window():
    calls = [call(i, model="old", agreed=False) for i in range(10)] + [call(i, model="new", day=2) for i in range(4)]
    s = summary_of(calls)
    assert (s.shadow, s.model_id, s.costly) == (4, "new", 0)


def test_a_claude_code_version_change_restarts_the_window():
    calls = [call(i, version="1") for i in range(10)] + [call(i, version="2", day=2) for i in range(3)]
    assert summary_of(calls).shadow == 3


def test_a_change_seen_only_on_a_non_shadow_row_still_restarts_the_window():
    calls = [call(i) for i in range(5)] + [call(0, model="new", mode=None, day=2), call(1, model="new", day=2)]
    assert summary_of(calls).shadow == 1


def test_since_drops_older_rows():
    calls = [call(0, day=1), call(0, day=5), call(1, day=5)]
    assert summary_of(calls, since="2026-09-05").shadow == 2


def test_a_role_with_no_shadow_rows_is_not_listed():
    rows = s1.rows_from_usage([{"calls": [call(0, role="handoff", mode="on"), call(1, role="review_charter")]}])
    assert [s.role for s in s1.summaries(rows, None, None)] == ["review_charter"]


def test_saved_per_week_is_covered_over_window_days_times_seven():
    s = summary_of([call(0, day=1), call(1, day=1), call(0, day=11, conf=0.1)])
    assert round(s.days, 3) == 10.0
    assert round(s.saved_per_week, 3) == 1.4


def test_the_report_names_the_unmet_bar_and_the_json_carries_the_verdict():
    s = summary_of([call(i) for i in range(3)])
    assert s1.render_report([s]).splitlines()[0] == "handoff: NOT YET (rows 3 < 100)"
    assert s1.to_json([s])["roles"][0]["verdict"] == "NOT YET"


def test_the_proposal_states_numbers_change_saving_and_risk():
    s = summary_of([call(i, day=1 + i // 50, agreed=i != 0) for i in range(200)] + [call(0, day=8, conf=0.1)])
    assert s1.verdict(s)[0] == "READY"
    assert s1.render_proposal(s, "2026-09-24") == """# System one graduation: handoff, 2026-09-24

Verdict: READY

Proposal only. Nothing was edited. The maintainer approves and makes the change.

## Numbers

Window 2026-09-01 to 2026-09-08 (7.0 days), model m1, claude_code v1. A change of either restarts the count.

- Shadow rows: 201 (bar 100)
- Covered rows: 200 (99.5% of shadow)
- Agreement among covered: 99.5%, 199 of 200 (bar 95%)
- Costly-direction disagreements: 1 (0.5% of covered, bar 1%)
- Other disagreements: 0

## The change

In the provider profile the harness runs with (for Claude Code, `providers/claude-code.yaml` in coxswain-cartridges), change the role's mode under `system_one.roles` from shadow to on. Keep its threshold.

    system_one.roles.handoff.mode:  shadow  ->  on

## What it saves

About 200.0 LLM calls per week for `handoff` would be skipped: 200 covered rows over 7.0 days, times 7.

## What it risks

The costly direction is the fast path answering `yes` where the LLM node would not have. In this window that happened 1 times in 200 covered rows (0.5%). With the mode on, those calls would pass without the LLM's check.
"""
