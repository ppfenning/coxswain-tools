import argparse
import json
import tomllib
from pathlib import Path

import pytest
from conftest import strip_ansi

from agent_tools import cli as cli_module
from agent_tools import epic
from agent_tools.cli import build_parser, main

LOG = """
  quarantined task: a — reason
  reused b from run-1 (approved patch, no model call)
epic run-2: 1 phase(s) complete, 0 partial, 0 blocked, 1 task(s) quarantined, 0 stack(s) rebased
  usage   : 5 node call(s), 20 turns, $1.00
"""


def test_summarize_log_picks_the_outcome_lines():
    s = epic.summarize_log(LOG)
    assert s["quarantined"] == ["quarantined task: a — reason"] and s["reused"][0].startswith("reused b")
    assert s["summary"].startswith("epic run-2") and s["usage"].startswith("usage")


def test_watch_returns_at_once_for_a_dead_pid(tmp_path):
    pf = tmp_path / "pid"; pf.write_text("999999999")
    log = tmp_path / "log"; log.write_text(LOG)
    out = epic.watch(pf, log=log, max_seconds=1, interval=0.1)
    assert out["finished"] and out["summary"].startswith("epic run-2")


def test_every_subcommand_parses():
    p = build_parser()
    for argv in (["runs", "usage", "r"], ["runs", "trace", "r", "--role", "build"], ["runs", "clean", "r", "--repo", "."],
                 ["epic", "watch", "pid"], ["plan", "serve", "d"]):
        assert p.parse_args(argv).fn


def test_runs_usage_recovers_totals_from_the_trace_when_live(tmp_path, monkeypatch, capsys):
    (tmp_path / "r.pid").write_text("4321")
    trace_dir = tmp_path / "r-trace"; trace_dir.mkdir()
    (trace_dir / "build-1.jsonl").write_text(json.dumps({"type": "result", "total_cost_usd": 1.5, "num_turns": 3}) + "\n")
    monkeypatch.setattr(cli_module.epic, "alive", lambda pid: True)
    rc = cli_module._runs_usage(argparse.Namespace(runs_dir=str(tmp_path), run_id="r", json=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "live (pid 4321) — from the trace so far" in out
    assert "build" in out and "1.50" in out


def test_runs_usage_recovers_totals_from_a_trace_only_directory_not_live(tmp_path, capsys):
    trace_dir = tmp_path / "r-trace"; trace_dir.mkdir()
    (trace_dir / "build-1.jsonl").write_text(json.dumps({"type": "result", "total_cost_usd": 1.5, "num_turns": 3}) + "\n")
    rc = cli_module._runs_usage(argparse.Namespace(runs_dir=str(tmp_path), run_id="r", json=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "not live — from the trace so far" in out
    assert "live (pid" not in out
    assert "build" in out and "1.50" in out


def test_runs_usage_recovers_totals_as_json_stays_valid_json_on_stdout(tmp_path, capsys):
    trace_dir = tmp_path / "r-trace"; trace_dir.mkdir()
    (trace_dir / "build-1.jsonl").write_text(json.dumps({"type": "result", "total_cost_usd": 1.5, "num_turns": 3}) + "\n")
    rc = cli_module._runs_usage(argparse.Namespace(runs_dir=str(tmp_path), run_id="r", json=True))
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["note"] == "not live — from the trace so far"
    assert payload["by_role"]["build"]["cost_usd"] == 1.5


def test_runs_usage_with_no_usage_file_and_no_trace_exits_2(tmp_path, capsys):
    rc = cli_module._runs_usage(argparse.Namespace(runs_dir=str(tmp_path), run_id="ghost", json=False))
    out = capsys.readouterr().out
    assert rc == 2
    assert str(tmp_path) in out


def test_runs_usage_with_a_usage_file_present_is_unchanged(tmp_path, capsys):
    usage = {"run_id": "r2", "calls": [{"role": "build", "model": "m", "cost_usd": 2.0, "turns": 4}]}
    (tmp_path / "r2.usage.json").write_text(json.dumps(usage))
    rc = cli_module._runs_usage(argparse.Namespace(runs_dir=str(tmp_path), run_id="r2", json=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "r2: 1 calls, 4 turns, $2.00" in out
    assert "live (pid" not in out


def test_cox_and_agent_tools_scripts_resolve_to_the_same_callable():
    data = tomllib.loads(Path(__file__).resolve().parent.parent.joinpath("pyproject.toml").read_text())
    scripts = data["project"]["scripts"]
    assert scripts["cox"] == scripts["agent-tools"]


def test_help_usage_names_cox(capsys, monkeypatch):
    monkeypatch.setenv("PYTHON_COLORS", "0")
    monkeypatch.setenv("NO_COLOR", "1")
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert strip_ansi(capsys.readouterr().out).startswith("usage: cox")
