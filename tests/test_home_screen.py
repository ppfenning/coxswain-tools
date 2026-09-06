import time

from agent_tools import home_model
from agent_tools.home_screen import _panel, _read_with_timeout, draw, facts, run_effect


def test_panel_status_is_fresh_within_timeout_stale_past_it_and_absent_with_no_value():
    assert home_model.panel_status("v", 1.0, 2.0) == "fresh"
    assert home_model.panel_status("v", 3.0, 2.0) == "stale"
    assert home_model.panel_status(None, 0.0, 2.0) == "absent"


def test_read_with_timeout_returns_none_for_a_slow_reader_within_bounded_time():
    started = time.monotonic()

    result = _read_with_timeout(lambda: time.sleep(0.5), timeout_seconds=0.05)

    assert result is None
    assert time.monotonic() - started < 0.3


def test_read_with_timeout_returns_none_for_a_raising_reader():
    def _raise():
        raise ValueError("boom")

    assert _read_with_timeout(_raise, timeout_seconds=0.05) is None


def test_panel_falls_back_to_the_cached_value_and_marks_stale_when_the_reader_times_out():
    cache = {"window": ({"tier": "standard"}, 100.0)}

    value, status, new_cache = _panel(cache, "window", lambda: time.sleep(0.5), timeout_seconds=0.05, now=105.0)

    assert value == {"tier": "standard"}
    assert status == "stale"
    assert new_cache == cache


def test_facts_reads_one_run_and_treats_a_missing_leader_file_as_none(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    (runs_dir / "r1.pid").write_text("123", encoding="utf-8")
    (runs_dir / "r1.log").write_text("n1 verdict: land\n", encoding="utf-8")
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    intake_dir = tmp_path / "intake"
    intake_dir.mkdir()

    result, cache = facts(runs_dir, work_dir, intake_dir, now=1_700_000_000.0)

    assert result.leader is None
    assert len(result.runs_rows) == 1
    assert cache["_status"]["leader"] == "fresh"


def test_facts_keeps_the_cached_window_when_its_reader_times_out(tmp_path, monkeypatch):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    intake_dir = tmp_path / "intake"
    intake_dir.mkdir()
    stale_window = {"tier": "cheap", "effort_ceiling": "low", "spent_usd": 1.0, "time_to_reset": "1m", "reason": "r"}
    cache = {"window": (stale_window, 0.0)}
    monkeypatch.setattr("agent_tools.home_screen._read_window", lambda *a, **k: time.sleep(0.5))

    result, new_cache = facts(runs_dir, work_dir, intake_dir, now=10.0, cache=cache, timeout_seconds=0.05)

    assert result.window == stale_window
    assert new_cache["_status"]["window"] == "stale"


class _FakeStdscr:
    def __init__(self, size=(24, 80)):
        self._size = size
        self.addnstr_calls = []

    def getmaxyx(self):
        return self._size

    def clear(self):
        pass

    def refresh(self):
        pass

    def addnstr(self, y, x, s, n, attr=0):
        self.addnstr_calls.append((y, x, s, n, attr))


def _facts_fixture():
    return home_model.Facts(
        leader={"session": "s1", "heartbeat_at": "2024-01-01T00:00:00+00:00"},
        leader_liveness="live",
        runs_rows=(),
        backlog={"queued": 1, "decomposed": 2, "landed": 3, "ready": {"init-a": 1}},
        window={"tier": "standard", "effort_ceiling": "high", "spent_usd": 1.5, "time_to_reset": "10m", "reason": "on pace"},
        now=1_704_067_200.0,
    )


def test_draw_contains_each_panes_expected_lines_and_marks_a_stale_panel():
    stdscr = _FakeStdscr()
    state = home_model.State(plugin_dir="/plugins/coxswain", leader_liveness="live", other_holder=None)
    statuses = {"leader": "fresh", "runs": "fresh", "backlog": "stale", "window": "fresh"}

    draw(stdscr, _facts_fixture(), state, statuses)

    text = [call[2] for call in stdscr.addnstr_calls]
    assert any("LEADER" in line for line in text)
    assert any("queued 1" in line for line in text)
    assert any("WINDOW" in line for line in text)
    assert any(line.startswith("! ") and "BACKLOG" in line for line in text)


def test_t_key_runs_claude_with_the_plugin_dir_and_opening():
    state = home_model.State(plugin_dir="/plugins/coxswain", leader_liveness="none", other_holder=None)
    _, effect = home_model.step(state, "t")
    calls = []

    stopped = run_effect(effect, runner=calls.append)

    assert calls == [["claude", "--plugin-dir", "/plugins/coxswain", home_model.OPENING_CONTEXT]]
    assert stopped is False


def test_s_key_runs_cox_setup():
    state = home_model.State(plugin_dir="", leader_liveness="none", other_holder=None)
    _, effect = home_model.step(state, "s")
    calls = []

    stopped = run_effect(effect, runner=calls.append)

    assert calls == [list(home_model.SETUP_ARGV)]
    assert stopped is False


def test_q_key_stops_the_loop_with_no_subprocess_call():
    state = home_model.State(plugin_dir="", leader_liveness="none", other_holder=None)
    _, effect = home_model.step(state, "q")
    calls = []

    stopped = run_effect(effect, runner=calls.append)

    assert calls == []
    assert stopped is True
