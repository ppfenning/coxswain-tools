import json
import os
import time

import pytest

from agent_tools import cli
from agent_tools.runs_top_screen import draw, facts, first_visible, loop, rows_now


def _write(path, text):
    path.write_text(text, encoding="utf-8")


def test_facts_reads_one_alive_run_with_calls_and_phases(tmp_path):
    _write(tmp_path / "r1.pid", "123")
    _write(tmp_path / "r1.log", "n1 verdict: land\n")
    _write(tmp_path / "r1:build.json", "{}")
    trace = tmp_path / "r1-trace"
    trace.mkdir()
    _write(trace / "n1-1.jsonl", json.dumps({"type": "result", "num_turns": 4, "total_cost_usd": 0.5}) + "\n")
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
        self.checkpoints = []

    def getmaxyx(self):
        return self._size

    def clear(self):
        self.draws += 1

    def refresh(self):
        pass

    def timeout(self, ms):
        pass

    def addnstr(self, y, x, s, n, attr=0):
        self.addnstr_calls.append((y, x, s, n, attr))

    def getch(self):
        self.checkpoints.append(len(self.addnstr_calls))
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


def test_loop_enter_expands_the_row_in_place_and_enter_again_collapses_it(tmp_path):
    _write(tmp_path / "r1.pid", "123")
    _write(tmp_path / "r1.log", "n1 verdict: land\n")
    stdscr = _FakeStdscr([ord("\n"), ord("\n"), ord("q")])

    rc = loop(stdscr, tmp_path, 1, tick=rows_now, now_alive=lambda pid: True)

    bounds = [0, *stdscr.checkpoints]
    draws = [stdscr.addnstr_calls[bounds[i]:bounds[i + 1]] for i in range(len(bounds) - 1)]
    assert rc == 0
    assert not any(c[2].startswith("  ") for c in draws[0])
    assert any(c[2].startswith("  ") for c in draws[1])
    assert not any(c[2].startswith("  ") for c in draws[2])


def test_draw_marks_the_cursor_row_with_a_reverse_attr(tmp_path):
    curses = pytest.importorskip("curses")
    _write(tmp_path / "r1.pid", "123")
    _write(tmp_path / "r1.log", "n1 verdict: land\n")
    stdscr = _FakeStdscr([])

    draw(stdscr, rows_now(tmp_path), cursor=0)

    row_call = next(c for c in stdscr.addnstr_calls if c[0] == 1)
    assert row_call[4] & curses.A_REVERSE


def test_loop_j_moves_the_cursor_so_enter_expands_the_second_row(tmp_path):
    from agent_tools import runs_top

    _write(tmp_path / "r1.pid", "123")
    _write(tmp_path / "r1.log", "n1 verdict: land\n")
    _write(tmp_path / "r2.pid", "124")
    _write(tmp_path / "r2.log", "n1 verdict: land\n")
    rows = [runs_top.row("r1", True, [], [], [], None), runs_top.row("r2", True, [], [], [], None)]
    stdscr = _FakeStdscr([ord("j"), ord("\n"), ord("q")])

    rc = loop(stdscr, tmp_path, 1, tick=lambda d: rows, now_alive=lambda pid: True)

    assert rc == 0
    assert any("run r2 [" in call[2] for call in stdscr.addnstr_calls)
    assert not any("run r1 [" in call[2] for call in stdscr.addnstr_calls)


def test_loop_enter_on_another_row_moves_the_expansion_there(tmp_path):
    from agent_tools import runs_top

    _write(tmp_path / "r1.pid", "123")
    _write(tmp_path / "r1.log", "n1 verdict: land\n")
    _write(tmp_path / "r2.pid", "124")
    _write(tmp_path / "r2.log", "n1 verdict: land\n")
    rows = [runs_top.row("r1", True, [], [], [], None), runs_top.row("r2", True, [], [], [], None)]
    stdscr = _FakeStdscr([ord("\n"), ord("j"), ord("\n"), ord("q")])

    rc = loop(stdscr, tmp_path, 1, tick=lambda d: rows, now_alive=lambda pid: True)

    bounds = [0, *stdscr.checkpoints]
    draws = [stdscr.addnstr_calls[bounds[i]:bounds[i + 1]] for i in range(len(bounds) - 1)]
    assert rc == 0
    assert any("run r1 [" in c[2] for c in draws[1])
    assert any("run r2 [" in c[2] for c in draws[3])
    assert not any("run r1 [" in c[2] for c in draws[3])


def test_first_visible_leaves_a_cursor_already_on_screen_alone():
    assert first_visible(cursor_index=7, total_lines=30, window_height=10, current_first=5) == 5


def test_first_visible_scrolls_the_minimum_to_bring_the_cursor_back_on_screen():
    assert first_visible(cursor_index=15, total_lines=30, window_height=10, current_first=0) == 6


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


def test_the_row_names_the_node_written_last_not_the_last_one_alphabetically(tmp_path):
    _write(tmp_path / "r1.pid", "123")
    _write(tmp_path / "r1.log", "")
    trace = tmp_path / "r1-trace"
    trace.mkdir()
    result = json.dumps({"type": "result", "num_turns": 1, "total_cost_usd": 0.1}) + "\n"
    _write(trace / "scope_epic-1.jsonl", result)
    _write(trace / "build-4.jsonl", result)
    os.utime(trace / "scope_epic-1.jsonl", (1000, 1000))
    os.utime(trace / "build-4.jsonl", (2000, 2000))

    rows = rows_now(tmp_path)

    assert (rows[0].node, rows[0].attempt) == ("build", 4)
