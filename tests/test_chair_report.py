from datetime import UTC, datetime

import pytest

from agent_tools.chair_read_quarantined import RUNAWAY_CAUSE
from agent_tools.chair_report import (
    Deps,
    echo_line,
    format_status,
    housekeeping_fragment,
    lands_this_tick,
    needs_chair_items,
    partial_land_text,
    write_status,
)
from agent_tools.chair_watchdog import StatusLine, idle, parse_status
from agent_tools.notify import Notification

NOW = datetime(2026, 9, 26, 18, 5, tzinfo=UTC)  # 14:05 EDT


def _facts(hard_stop=False, weekly=0.61, five=0.42, last_housekeeping_at=None, window_start_day=None,
           five_hour_source="meter", weekly_source="meter", review_prs=None, idle_stall=None):
    return {
        **({} if review_prs is None else {"review_prs": review_prs}),
        **({} if idle_stall is None else {"idle_stall": idle_stall}),
        "limits": {"hard_stop": hard_stop, "weekly_fraction": weekly, "hard_stop_fraction": 0.9,
                   "launch_cap": 2, "go_degraded": False, "five_hour_fraction": five,
                   "window_start_day": window_start_day, "window_source": five_hour_source,
                   "weekly_source": weekly_source},
        "dispatch": {"max_in_flight": 4, "live_runs": 2},
        "last_housekeeping_at": last_housekeeping_at,
    }


def _landed(kind="land", status="landed"):
    return {"action": {"kind": kind}, "status": status, "reason": ""}


def _partial(task_ids, quarantined):
    return {"kind": "partial_land", "initiative": "i", "phase": "p", "task_ids": task_ids, "quarantined": quarantined}


def test_status_line_round_trips_through_parse_status():
    actions = [
        {"kind": "needs_chair", "initiative": "epic-a", "cause": "harness"},
        {"kind": "needs_chair", "initiative": "b", "cause": RUNAWAY_CAUSE},
        _partial(["a"], 1),
    ]
    parsed = parse_status(format_status(_facts(), actions, [_landed()], NOW))
    assert parsed is not None
    assert (parsed.busy, parsed.total) == (2, 4)
    assert isinstance(parsed, StatusLine)
    assert "epic-a:harness" in parsed.holds
    assert "RUNAWAY b" in parsed.holds
    assert any(h.startswith("partial-land: ") for h in parsed.holds)
    assert len(parsed.holds) == 3


def test_status_line_lanes_count_every_host():
    hosts = [{"name": "a", "live_runs": 4, "capacity": 6}, {"name": "b", "live_runs": 6}]
    facts = {**_facts(), "dispatch": {"max_in_flight": 4, "live_runs": 0, "hosts": hosts}}
    assert format_status(facts, [], [], NOW).split(" | ")[1] == "lanes 10/10 (here 0/4)"


def test_fleet_status_line_still_parses_and_idle_alert_fires():
    hosts = [{"name": "a", "live_runs": 0, "capacity": 6}, {"name": "b", "live_runs": 0}]
    facts = {**_facts(), "dispatch": {"max_in_flight": 4, "live_runs": 0, "hosts": hosts}}
    hold = [{"kind": "needs_chair", "initiative": "epic-a", "cause": "harness"}]
    line = format_status(facts, hold, [], NOW)
    parsed = parse_status(line)
    assert parsed is not None
    assert (parsed.busy, parsed.total) == (0, 10)
    alert = idle([(NOW, line)], NOW)
    assert alert is not None
    assert alert.title == "Lanes idle 30 min"


def test_needs_chair_items_orders_runaway_first_and_partial_land_last():
    actions = [
        _partial(["a", "b"], 1),
        {"kind": "needs_chair", "initiative": "a", "cause": "budget"},
        {"kind": "needs_chair", "initiative": "b", "cause": RUNAWAY_CAUSE},
        {"kind": "needs_chair", "initiative": "c", "cause": "scope"},
    ]
    assert needs_chair_items(actions) == [
        "RUNAWAY b",
        "needs chair: a:budget",
        "needs chair: c:scope",
        "partial-land: i/p 2 approved, 1 quarantined",
    ]


def test_partial_land_text_names_approved_and_quarantined_counts():
    assert partial_land_text(_partial(["a"], 1)) == "partial-land: i/p 1 approved, 1 quarantined"
    assert partial_land_text(_partial(["a", "b", "c"], 2)) == "partial-land: i/p 3 approved, 2 quarantined"


def test_standby_line_names_the_holder_and_host():
    actions = [{"kind": "standby", "holder": "chair-7", "host": "box-1"}]
    line = format_status(_facts(), actions, [_landed("standby", "recorded")], NOW)
    assert "standby holder=chair-7 host=box-1" in line
    assert "lands 0" in line and "needs chair: none" in line


def test_hard_stop_line_says_hard_stop():
    line = format_status(_facts(hard_stop=True, weekly=0.95), [], [], NOW)
    assert "weekly 95%/90% (meter) hard stop" in line
    assert "hard stop" not in format_status(_facts(), [], [], NOW)


def test_limit_fragments_name_their_source_and_the_since_day():
    meter = format_status(_facts(), [], [], NOW)
    est = format_status(_facts(five_hour_source="est", weekly_source="est"), [], [], NOW)
    store = format_status(_facts(five_hour_source="meter, store", weekly_source="meter, store"), [], [], NOW)
    since = format_status(_facts(window_start_day="Sun 04:00"), [], [], NOW)
    assert "5h 42% (meter)" in meter and "weekly 61%/90% (meter)" in meter
    assert "5h 42% (est)" in est and "weekly 61%/90% (est)" in est
    assert "5h 42% (meter, store)" in store and "weekly 61%/90% (meter, store)" in store
    assert "weekly 61%/90% (meter) since Sun 04:00" in since
    assert "since" not in meter


def test_pace_target_shows_only_when_judged():
    def with_pace(facts, **pace):
        return {**facts, "limits": {**facts["limits"], **pace}}

    base = _facts(window_start_day="Sun 04:00")
    judged = format_status(with_pace(base, weekly_target_fraction=0.45, weekly_pace="reduced"), [], [], NOW)
    plain = format_status(base, [], [], NOW)
    assert "target 45% reduced" in judged
    assert "target" not in plain
    not_judged = format_status(with_pace(base, weekly_target_fraction=0.45, weekly_pace="not_judged"), [], [], NOW)
    no_target = format_status(with_pace(base, weekly_target_fraction=None, weekly_pace="not_judged"), [], [], NOW)
    assert "target" not in not_judged
    assert "target" not in no_target


def _result(kind, status, **fields):
    return {"action": {"kind": kind, **fields}, "status": status, "reason": ""}


def _steer(initiative, other):
    return _result("steer_clear", "recorded", initiative=initiative, other=other, paths=["src/main.rs"])


def test_launched_names_the_action_and_the_lane_host():
    results = [
        _result("rescue", "done", initiative="epic-a", task_id="t1"),
        _result("retry", "done", initiative="epic-b"),
        _result("launch_epic", "done", initiative="x", host="host-a"),
        _result("launch_epic", "fenced", initiative="y"),
    ]
    line = format_status(_facts(), [], results, NOW)
    assert "launched: rescue:epic-a" in line
    assert "retry:epic-b" in line
    assert "epic:x@host-a" in line
    assert "launch_epic" not in line
    assert "epic:y" not in line
    assert "failed:" not in line and "fetched:" not in line


def test_failed_covers_land_land_phase_and_fetch_exit():
    results = [
        _result("land", "not_landed", task_id="t1", initiative="epic-a"),
        _result("land_phase", "not_landed", phase="p2", initiative="epic-a"),
        _result("pull", "refused", initiative="epic-c"),
        _result("fetch_exit", "failed", run="epic-a-1"),
    ]
    line = format_status(_facts(), [], results, NOW)
    assert "failed: land:t1" in line
    assert "land_phase:epic-a/p2" in line
    assert "pull:epic-c" in line
    assert "fetch_exit:?" in line
    assert "fetched:" not in line


def test_fetched_names_the_run_and_host():
    with_host = format_status(_facts(), [], [_result("fetch_exit", "done", run="epic-a-1", host="host-a")], NOW)
    no_host = format_status(_facts(), [], [_result("fetch_exit", "done", run="epic-a-1", host=None)], NOW)
    assert "fetched: epic-a-1 from host-a" in with_host
    assert "fetched: epic-a-1 from on another machine" in no_host


def test_dry_run_names_what_it_would_do():
    would = format_status(_facts(), [], [_result("launch_epic", "dry_run", initiative="x")], NOW)
    landed = format_status(_facts(), [{"kind": "land"}], [_landed("land", "dry_run")], NOW)
    assert "dry-run" in would and "would: launch_epic:x" in would
    assert "dry-run" in landed and "holding" not in landed


def test_steered_shows_each_pair_and_no_paths():
    line = format_status(_facts(), [], [_steer("x", "y"), _steer("z", "w")], NOW)
    assert "steered: x~y, z~w" in line
    assert "src/main.rs" not in line
    assert "steered" not in format_status(_facts(), [], [], NOW)


def test_lands_this_tick_counts_land_land_phase_and_partial_land_only_when_landed():
    assert lands_this_tick([_landed(), _landed(status="not_landed"), _landed("clear_branches", "done")]) == 1
    assert lands_this_tick([_landed("land_phase"), _landed("land_phase", status="not_landed")]) == 1
    assert lands_this_tick([{"action": _partial(["a"], 1), "status": "landed", "reason": ""}]) == 1


def test_write_status_notifies_only_when_given():
    printed, sent = [], []
    write_status("line", Deps(echo=printed.append))
    write_status("line", Deps(echo=printed.append, notify=sent.append))
    assert printed == ["line", "line"]
    assert sent == [Notification("chair tick", "line", "low")]


def _review_pr(task_id, url):
    return {"initiative": "epic-a", "phase": "p1", "task_id": task_id, "repo": "r", "url": url,
            "state": "unknown", "merged_at": None}


_PRS = [_review_pr("t-1", "https://x/pull/1"), _review_pr("t-2", "https://x/pull/2")]


@pytest.mark.parametrize(
    ("extra", "marker", "present", "after"),
    [
        ({"drafts": 3}, "drafts 3", True, "lands 0"),
        ({"drafts": 0}, "drafts", False, None),
        ({"missing_repos": ["/repo/a"]}, "skipped missing repo /repo/a", True, None),
        ({"missing_repos": []}, "missing repo", False, None),
        ({"review_prs": _PRS}, "review: 2 awaiting (t-1 https://x/pull/1, t-2 https://x/pull/2)", True, None),
        ({"review_prs": []}, "review:", False, None),
        ({"idle_stall": {"open_diagnosis": "host host-a ssh check failed"}}, "stall: host host-a ssh check failed", True, "limits "),
        ({"idle_stall": {"open_diagnosis": None}}, "stall:", False, None),
        ({}, "stall:", False, None),
    ],
    ids=["drafts", "no-drafts", "missing-repo", "no-missing-repo", "review", "no-review", "stall", "stall-no-diagnosis", "stall-no-key"],
)
def test_optional_sections_appear_only_when_present(extra, marker, present, after):
    line = format_status({**_facts(), **extra}, [], [], NOW)
    assert (marker in line) is present
    assert after is None or line.index(after) < line.index(marker)


def test_standby_line_with_an_until_says_who_holds_the_chair_until_when():
    actions = [{"kind": "standby", "holder": "chair-7@box-1:42", "host": "box-1", "until": "2026-09-26T15:00:00+00:00"}]
    line = format_status(_facts(), actions, [_landed("standby", "recorded")], NOW)
    assert "standby: held by chair-7 until 2026-09-26T15:00:00+00:00" in line


def test_echo_line_flushes_stdout_after_the_line(monkeypatch):
    class Stub:
        def __init__(self):
            self.calls = []

        def write(self, text):
            self.calls.append(("write", text))

        def flush(self):
            self.calls.append(("flush", ""))

    stub = Stub()
    monkeypatch.setattr("sys.stdout", stub)
    write_status("chair tick", Deps(echo=echo_line))
    assert stub.calls[-1] == ("flush", "")
    assert ("write", "chair tick") in stub.calls


def test_housekeeping_fragment_ages():
    assert housekeeping_fragment("2026-09-26T17:53:00+00:00", NOW) == "housekeeping 12m"
    assert housekeeping_fragment("2026-09-26T15:05:00+00:00", NOW) == "housekeeping 3h"
    assert housekeeping_fragment("2026-09-24T16:05:00+00:00", NOW) == "housekeeping 2d"
    assert housekeeping_fragment(None, NOW) == "housekeeping never"
    assert housekeeping_fragment("2026-09-26T15:05:00", NOW) == "housekeeping never"
