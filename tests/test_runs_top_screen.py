import json
import time

import pytest

from agent_tools import cli
from agent_tools.runs_top_screen import draw, facts, loop, rows_now


def _write(path, text):
    path.write_text(text, encoding="utf-8")


def test_facts_reads_one_alive_run_with_calls_and_phases(tmp_path):
    _write(tmp_path / "r1.pid", "123")
    _write(tmp_path / "r1.log", "n1 verdict: land\n")
    _write(tmp_path / "r1:build.json", "{}")
    trace = tmp_path / "r1-trace"
    trace.mkdir()
    _write(trace / "n1-1.jsonl", json.dumps({"type": "result", "total_cost_usd": 0.5, "num_turns": 4}) + "\n")
    _write(trace / "n2-1.jsonl", json.dumps({"type": "assistant"}) + "\n")

    result = facts(tmp_path, now_alive=lambda pid: pid == 123)

    assert len(result) == 1
    f = result[0]
    assert f["run"] == "r1"
    assert f["alive"] is True
    assert f["phases"] == ["build"]
    assert f["calls"] == [{"node": "n1", "attempt": 1, "cost_usd": 0.5, "turns": 4}]


def test_rows_now_maps_facts_through_runs_top_row(tmp_path):
    assert rows_now(tmp_path) == []


def test_facts_reads_the_runs_ceiling_file(tmp_path):
    _write(tmp_path / "r1.pid", "123")
    _write(tmp_path / "r1.log", "n1 verdict: land\n")
    _write(tmp_path / "r1.ceiling.json", json.dumps(
        {"requested": {"tier": "standard", "effort": None}, "applied": {"tier": "standard", "effort": "high"}, "profile": "p.yaml"}))

    result = facts(tmp_path, now_alive=lambda pid: pid == 123)

    assert result[0]["ceiling"]["applied"] == {"tier": "standard", "effort": "high"}


def test_facts_ceiling_is_none_with_no_ceiling_file(tmp_path):
    _write(tmp_path / "r1.pid", "123")
    _write(tmp_path / "r1.log", "n1 verdict: land\n")

    result = facts(tmp_path, now_alive=lambda pid: pid == 123)

    assert result[0]["ceiling"] is None


def test_rows_now_carries_the_ceiling_label_into_the_row(tmp_path):
    _write(tmp_path / "r1.pid", "123")
    _write(tmp_path / "r1.log", "n1 verdict: land\n")
    _write(tmp_path / "r1.ceiling.json", json.dumps(
        {"requested": {"tier": "standard", "effort": None}, "applied": {"tier": "standard", "effort": "high"}, "profile": "p.yaml"}))

    rows = rows_now(tmp_path)

    assert rows[0].ceiling == "standard/high"


def test_facts_omits_a_dead_run_with_a_stale_log(tmp_path):
    _write(tmp_path / "r2.pid", "999")
    log = tmp_path / "r2.log"
    _write(log, "old\n")
    old = time.time() - 3600
    import os
    os.utime(log, (old, old))

    result = facts(tmp_path, now_alive=lambda pid: False)

    assert result == []


class _FakeStdscr:
    def __init__(self, keys, size=(24, 80)):
        self._keys = list(keys)
        self._size = size
        self.draws = 0
        self.addnstr_calls = []

    def getmaxyx(self):
        return self._size

    def clear(self):
        self.draws += 1

    def refresh(self):
        pass

    def timeout(self, ms):
        pass

    def addnstr(self, y, x, s, n, attr=0):
        self.addnstr_calls.append((y, x, s, n))

    def getch(self):
        return self._keys.pop(0)


def test_draw_never_writes_a_line_wider_than_the_fake_width():
    stdscr = _FakeStdscr([], size=(24, 10))
    draw(stdscr, [])
    assert stdscr.addnstr_calls
    assert all(len(call[2]) <= call[3] for call in stdscr.addnstr_calls)


def test_loop_returns_0_on_an_immediate_q():
    stdscr = _FakeStdscr([ord("q")])
    rc = loop(stdscr, "unused", 1, tick=lambda d: [])
    assert rc == 0
    assert stdscr.draws == 1


def test_loop_redraws_once_on_a_resize_then_exits_on_q():
    curses = pytest.importorskip("curses")
    stdscr = _FakeStdscr([curses.KEY_RESIZE, ord("q")])
    rc = loop(stdscr, "unused", 1, tick=lambda d: [])
    assert rc == 0
    assert stdscr.draws == 2


def test_cli_runs_top_once_prints_the_header(tmp_path, capsys):
    rc = cli.main(["runs", "top", "--runs-dir", str(tmp_path), "--once"])
    assert rc == 0
    assert "RUN" in capsys.readouterr().out


def test_cli_runs_top_once_prints_ceil_for_a_run_with_a_ceiling_file(tmp_path, capsys):
    _write(tmp_path / "r1.pid", "123")
    _write(tmp_path / "r1.log", "n1 verdict: land\n")
    _write(tmp_path / "r1.ceiling.json", json.dumps(
        {"requested": {"tier": "standard", "effort": None}, "applied": {"tier": "standard", "effort": "high"}, "profile": "p.yaml"}))

    rc = cli.main(["runs", "top", "--runs-dir", str(tmp_path), "--once"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "CEIL" in out
    assert "standard/high" in out
