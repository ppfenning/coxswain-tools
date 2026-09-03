from agent_tools import epic
from agent_tools.cli import build_parser

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
                 ["epic", "watch", "pid"], ["hud", "ops", "-"], ["hud", "say", "hi"], ["hud", "inbox", "arm"], ["hud", "cast"], ["plan", "serve", "d"]):
        assert p.parse_args(argv).fn
