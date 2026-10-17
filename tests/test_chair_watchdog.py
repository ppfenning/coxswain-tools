import json
import sqlite3
from datetime import UTC, datetime, timedelta

from agent_tools.chair_watchdog import (
    Alert,
    StatusLine,
    evaluate,
    idle,
    loop_down,
    parse_status,
    read_lease_row,
    read_status_rows,
    run_watchdog,
    status_rows,
)
from agent_tools.notify_core import NotifyConfig
from agent_tools.notify_dispatch import dispatch

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


CONFIG = NotifyConfig("https://ntfy.example/topic")
FRESH_BEAT = NOW - timedelta(minutes=1)
STALE_BEAT = NOW - timedelta(minutes=20)
BUSY = _rows(*[_line("1/4", "needs chair: X:held")] * 6)
IDLE_HELD = _rows(*[_line("0/4", "needs chair: X:held")] * 6)


class Pushes:
    """A stub `post` that records (url, title, body) and reports success."""

    def __init__(self):
        self.sent = []

    def __call__(self, url, title, body, click=""):
        self.sent.append((url, title, body))
        return True


def _watch(tmp_path, post, *, beat=FRESH_BEAT, rows=BUSY, config=CONFIG, **options):
    def notifier(events, cfg, path, now):
        return dispatch(events, cfg, path, now, post=post)

    return run_watchdog(
        tmp_path,
        notify_config=config,
        state_path=tmp_path / "notify-sent.json",
        clock=lambda: NOW,
        notifier=notifier,
        read_lease=lambda _dir: ("host-a", beat),
        read_rows=lambda _dir, _now, _minutes: rows,
        **options,
    )


def test_stale_heartbeat_pushes_exactly_once(tmp_path):
    post = Pushes()
    assert _watch(tmp_path, post, beat=STALE_BEAT) == 0
    assert [(t, "host-a" in b) for _, t, b in post.sent] == [("Chair loop down", True)]


def test_idle_window_pushes_lanes_idle_with_the_hold(tmp_path):
    post = Pushes()
    assert _watch(tmp_path, post, rows=IDLE_HELD) == 0
    assert [t for _, t, _ in post.sent] == ["Lanes idle 30 min"] and "X:held" in post.sent[0][2]


def test_one_busy_row_in_the_window_pushes_nothing(tmp_path):
    post = Pushes()
    assert _watch(tmp_path, post, rows=_rows(_line("0/4", "needs chair: X:held"), _line("1/4", "needs chair: X:held"))) == 0
    assert post.sent == []


def test_needs_chair_none_without_waiting_pushes_nothing(tmp_path):
    post = Pushes()
    assert _watch(tmp_path, post, rows=_rows(*[_line("0/4", "needs chair: none")] * 6)) == 0
    assert post.sent == []


def test_repeat_inside_the_due_window_pushes_once(tmp_path):
    post = Pushes()
    assert [_watch(tmp_path, post, rows=IDLE_HELD) for _ in range(2)] == [0, 0]
    assert len(post.sent) == 1


def test_dry_run_prints_title_and_body_and_pushes_nothing(tmp_path, capsys):
    post = Pushes()
    assert _watch(tmp_path, post, beat=STALE_BEAT, dry_run=True) == 0
    out = capsys.readouterr().out
    assert out.startswith("Chair loop down\nHolder host-a last beat ") and post.sent == []
    assert not (tmp_path / "notify-sent.json").exists()


def test_idle_and_stale_minutes_options_reach_the_core(tmp_path):
    post = Pushes()
    assert _watch(tmp_path, post, beat=NOW - timedelta(minutes=8), stale_minutes=5, rows=IDLE_HELD, idle_minutes=15) == 0
    assert [t for _, t, _ in post.sent] == ["Chair loop down", "Lanes idle 15 min"]


def test_notifier_off_is_exit_zero(tmp_path):
    post = Pushes()
    assert _watch(tmp_path, post, beat=STALE_BEAT, config=None) == 0
    assert post.sent == []


def test_unreadable_store_is_nonzero_with_stderr_and_no_push(tmp_path, capsys):
    post = Pushes()

    def broken(*_args):
        raise sqlite3.OperationalError("no such table: leases")

    code = run_watchdog(
        tmp_path, notify_config=CONFIG, state_path=tmp_path / "s.json", clock=lambda: NOW,
        notifier=post, read_lease=broken,
    )
    err = capsys.readouterr().err
    assert code == 1 and "cannot read store" in err and "no such table" in err and post.sent == []


def test_missing_store_file_is_nonzero(tmp_path, capsys):
    post = Pushes()
    code = run_watchdog(tmp_path, notify_config=CONFIG, state_path=tmp_path / "s.json", clock=lambda: NOW,
                        notifier=post)
    assert code == 1 and "no store" in capsys.readouterr().err and post.sent == []


def test_failed_notifier_is_still_exit_zero(tmp_path, capsys):
    def boom(*_args):
        raise OSError("disk full")

    code = run_watchdog(tmp_path, notify_config=CONFIG, state_path=tmp_path / "s.json", clock=lambda: NOW,
                        notifier=boom, read_lease=lambda _d: ("h", STALE_BEAT), read_rows=lambda *_a: BUSY)
    assert code == 0 and "notify failed" in capsys.readouterr().err


def test_status_rows_keeps_window_lines_and_drops_the_rest():
    line = _line("0/4", "needs chair: X:held")
    cell = json.dumps({"kind": "status", "line": line})
    five = (NOW - timedelta(minutes=5)).isoformat()
    raw = [
        (five, cell),
        (five.replace("+00:00", "Z"), cell),
        ((NOW - timedelta(minutes=31)).isoformat(), cell),
        ("not a time", cell),
        (NOW.isoformat(), json.dumps({"kind": "status"})),
        (NOW.isoformat(), "not json"),
    ]
    assert [text for _, text in status_rows(raw, NOW, 30)] == [line, line]


def _store(tmp_path, *, lease=True, actions=()):
    conn = sqlite3.connect(tmp_path / "cox.db")
    conn.execute("CREATE TABLE leases (name TEXT PRIMARY KEY, holder TEXT, epoch INTEGER, heartbeat_at TEXT, expires_at TEXT, status TEXT)")
    conn.execute("CREATE TABLE chair_actions (kind TEXT, ts TEXT, action_json TEXT)")
    if lease:
        conn.execute("INSERT INTO leases VALUES ('chair', 'host-a', 7, ?, ?, 'held')", (STALE_BEAT.isoformat(), NOW.isoformat()))
    conn.executemany("INSERT INTO chair_actions VALUES (?, ?, ?)", actions)
    conn.commit()
    conn.close()


def test_readers_read_the_chair_lease_and_status_rows_from_a_real_store(tmp_path):
    line = _line("0/4", "needs chair: X:held")
    _store(tmp_path, actions=[
        ("status", (NOW - timedelta(minutes=3)).isoformat(), json.dumps({"line": line})),
        ("status", (NOW - timedelta(minutes=90)).isoformat(), json.dumps({"line": "old"})),
        ("land", (NOW - timedelta(minutes=2)).isoformat(), json.dumps({"line": "not a status row"})),
    ])
    assert read_lease_row(tmp_path) == ("host-a", STALE_BEAT)
    assert read_status_rows(tmp_path, NOW, 30) == [(NOW - timedelta(minutes=3), line)]


def test_absent_lease_row_reads_as_none_and_pages_loop_down(tmp_path, capsys):
    _store(tmp_path, lease=False)
    assert read_lease_row(tmp_path) == (None, None)
    code = run_watchdog(tmp_path, notify_config=CONFIG, state_path=tmp_path / "s.json", clock=lambda: NOW, dry_run=True)
    assert code == 0 and "No chair lease row" in capsys.readouterr().out
