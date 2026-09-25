from __future__ import annotations

import json
import os

from agent_tools import cli, runs_bar

_RUNNING = {"alive": True, "status": "running", "phase": "build", "node": "coder", "cost_usd": 7.9}
_EXITED = {"alive": False, "status": "exited", "phase": "build", "node": "coder", "cost_usd": 1.0}
_BUDGET = {"alive": True, "status": "budget", "phase": "review", "node": "critic", "cost_usd": 0.4}


def test_attention_is_true_when_a_row_is_quarantined_or_budget_stopped():
    assert runs_bar.attention([_RUNNING, _BUDGET]) is True


def test_attention_is_false_when_no_row_is_quarantined_or_budget_stopped():
    assert runs_bar.attention([_RUNNING, _EXITED]) is False


def test_bar_is_idle_with_no_live_runs():
    assert runs_bar.bar([], False) == {"text": "0 runs · $0.00", "tooltip": "all lanes clear", "class": "idle"}


def test_bar_is_running_with_a_live_run_and_no_attention():
    assert runs_bar.bar([_RUNNING], False) == {
        "text": "1 runs · $7.90", "tooltip": "build · coder", "class": "running",
    }


def test_bar_is_attention_when_attention_is_true():
    assert runs_bar.bar([_RUNNING, _BUDGET], True) == {
        "text": "2 runs · $8.30", "tooltip": "build · coder\nreview · critic", "class": "attention",
    }


def test_cli_runs_bar_prints_the_bar_built_from_a_real_run(tmp_path, capsys):
    """The keys runs_top.row()'s dataclasses.asdict output carries (status, alive,
    phase, node, cost_usd) are the same keys attention() and bar() index into."""
    (tmp_path / "r1.pid").write_text(str(os.getpid()), encoding="utf-8")
    (tmp_path / "r1.log").write_text("n1 verdict: land\n", encoding="utf-8")
    (tmp_path / "r1:build.json").write_text("{}", encoding="utf-8")
    trace = tmp_path / "r1-trace"
    trace.mkdir()
    (trace / "n1-1.jsonl").write_text(
        json.dumps({"type": "result", "num_turns": 4, "total_cost_usd": 0.5}) + "\n", encoding="utf-8")

    exit_code = cli.main(["runs", "bar", "--runs-dir", str(tmp_path)])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {
        "text": "1 runs · $0.50", "tooltip": "build · n1", "class": "running",
    }
