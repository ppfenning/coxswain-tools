from datetime import UTC, datetime, timedelta

from agent_tools.chair_watchdog import Alert, StatusLine, evaluate, idle, loop_down, parse_status

NOW = datetime(2026, 10, 17, 14, 0, tzinfo=UTC)
LIMITS = "limits 5h 12% wk 40%"
WAITING = "waiting: init-a (blocked by p1: 1 quarantined, 2 awaiting land)"


def _line(lanes: str, *tail: str) -> str:
    return " | ".join(["chair 10-17 10:00 EDT", f"lanes {lanes}", "lands 0", LIMITS, "idle", *tail])


def _rows(*lines: str) -> list[tuple[datetime, str]]:
    return [(NOW - timedelta(minutes=5 * (i + 1)), line) for i, line in enumerate(lines)]


def test_parse_reads_lanes_and_needs_chair():
    assert parse_status(_line("2/4", "needs chair: T-1:held, RUNAWAY x")) == StatusLine(2, 4, ("T-1:held", "RUNAWAY x"))


def test_parse_needs_chair_none_is_no_hold():
    assert parse_status(_line("0/4", "needs chair: none")) == StatusLine(0, 4, ())


def test_parse_waiting_is_one_hold_despite_inner_comma():
    expected = "init-a (blocked by p1: 1 quarantined, 2 awaiting land)"
    assert parse_status(_line("0/4", WAITING, "needs chair: none")) == StatusLine(0, 4, (expected,))


def test_parse_partial_land_keeps_its_comma():
    item = "partial-land: i/p 2 approved, 1 quarantined"
    assert parse_status(_line("0/4", f"needs chair: T-1:held, {item}")) == StatusLine(0, 4, ("T-1:held", item))


def test_parse_line_without_lanes_is_unreadable():
    assert parse_status("chair 10-17 10:00 EDT | lands 0 | needs chair: none") is None


def test_stale_heartbeat_names_holder_and_last_beat():
    beat = NOW - timedelta(minutes=11)
    alert = loop_down("host-a", beat, NOW)
    assert alert is not None and alert.title == "Chair loop down"
    assert "host-a" in alert.body and beat.isoformat() in alert.body


def test_fresh_heartbeat_is_no_alert():
    assert loop_down("host-a", NOW - timedelta(minutes=10), NOW) is None


def test_absent_lease_row_is_loop_down_with_unknown_holder():
    alert = loop_down(None, None, NOW)
    assert alert is not None and alert.title == "Chair loop down" and "unknown" in alert.body


def test_idle_window_with_hold_alerts():
    alert = idle(_rows(_line("0/4", "needs chair: X:held"), _line("0/4", "needs chair: X:held")), NOW)
    assert alert is not None and alert.title == "Lanes idle 30 min" and alert.body.count("X:held") == 1


def test_one_busy_row_is_no_alert():
    assert idle(_rows(_line("0/4", "needs chair: X:held"), _line("1/4", "needs chair: X:held")), NOW) is None


def test_unreadable_row_is_no_alert():
    assert idle(_rows(_line("0/4", "needs chair: X:held"), "chair 10-17 | lands 0"), NOW) is None


def test_needs_chair_none_without_waiting_is_no_alert():
    assert idle(_rows(_line("0/4", "needs chair: none")), NOW) is None


def test_waiting_alone_counts_as_hold():
    alert = idle(_rows(_line("0/4", WAITING, "needs chair: none")), NOW)
    assert alert is not None and "init-a" in alert.body


def test_row_outside_window_is_ignored():
    old = (NOW - timedelta(minutes=31), _line("3/4", "needs chair: none"))
    alert = idle([*_rows(_line("0/4", "needs chair: X:held")), old], NOW)
    assert alert is not None and alert.title == "Lanes idle 30 min"


def test_idle_minutes_is_named_in_title():
    alert = idle(_rows(_line("0/4", "needs chair: X:held")), NOW, idle_minutes=15)
    assert alert is not None and alert.title == "Lanes idle 15 min"


def test_empty_window_is_loop_down():
    assert idle([], NOW) == Alert("Chair loop down", "No status row seen in 30 min.")


def test_evaluate_fresh_beat_and_busy_window_is_empty():
    assert evaluate("host-a", NOW, _rows(_line("1/4", "needs chair: X:held")), NOW) == ()


def test_evaluate_idle_alert_alone():
    result = evaluate("host-a", NOW, _rows(_line("0/4", "needs chair: X:held")), NOW)
    assert [a.title for a in result] == ["Lanes idle 30 min"]


def test_stale_heartbeat_plus_empty_window_pages_once():
    result = evaluate("host-a", NOW - timedelta(minutes=20), [], NOW)
    assert len(result) == 1 and result[0].title == "Chair loop down" and "host-a" in result[0].body
