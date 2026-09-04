import json
import os

from agent_tools import route
from agent_tools.cli import main


def test_context_text_with_no_profile_mentions_no_profile(tmp_path, capsys):
    missing = tmp_path / "no-such-profile.yaml"
    rc = main(["route", "context", "--profile", str(missing)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "no profile" in out
    assert out.count("\n") == 1  # one line, no counts


def test_context_json_with_no_profile_has_reason_and_empty_lists(tmp_path, capsys):
    missing = tmp_path / "no-such-profile.yaml"
    rc = main(["route", "context", "--profile", str(missing), "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert "no profile" in doc["reason"]
    assert doc["intake"] == [] and doc["runs"] == [] and doc["initiatives"] == []
    assert doc["team"] is None


def test_context_text_with_profile_missing_workspace_dir_has_reason_on_first_line(tmp_path, capsys):
    profile = tmp_path / "profile.yaml"
    profile.write_text("team: acme\n")
    rc = main(["route", "context", "--profile", str(profile)])
    out = capsys.readouterr().out
    assert rc == 0
    first_line = out.splitlines()[0]
    assert "workspace_dir not set in profile" in first_line


def test_context_json_with_profile_missing_workspace_dir_has_reason_and_empty_lists(tmp_path, capsys):
    profile = tmp_path / "profile.yaml"
    profile.write_text("team: acme\n")
    rc = main(["route", "context", "--profile", str(profile), "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert doc["reason"] == "workspace_dir not set in profile"
    assert doc["intake"] == [] and doc["runs"] == [] and doc["initiatives"] == []
    assert doc["team"] == "acme"


def _write_workspace(tmp_path):
    ws = tmp_path / "workspace"
    (ws / "intake").mkdir(parents=True)
    (ws / "runs").mkdir(parents=True)
    (ws / "work" / "demo" / "1-build").mkdir(parents=True)
    (ws / "intake" / "one.md").write_text("---\nid: i1\ntitle: Do the thing\n---\n\nBody\n")
    (ws / "runs" / "run1.pid").write_text(str(os.getpid()))
    (ws / "runs" / "run2.pid").write_text("garbage")
    (ws / "work" / "demo" / "1-build" / "task.md").write_text("---\nstate: ready\n---\n\nDo the task\n")
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"team: acme\nworkspace_dir: {ws}\n")
    return profile


def test_context_json_with_full_profile_lists_initiative_and_runs(tmp_path, capsys):
    profile = _write_workspace(tmp_path)
    rc = main(["route", "context", "--profile", str(profile), "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert doc["initiatives"] == [{"id": "demo", "phase": "1-build", "ready": 1}]
    runs = {row["id"]: row for row in doc["runs"]}
    assert len(runs) == 2
    assert runs["run1"]["pid"] == os.getpid() and runs["run1"]["alive"] is True
    assert runs["run2"]["pid"] is None and runs["run2"]["alive"] is False
    assert doc["intake"] == [{"id": "i1", "title": "Do the thing"}]


def test_context_text_with_full_profile_lists_initiative_and_runs(tmp_path, capsys):
    profile = _write_workspace(tmp_path)
    rc = main(["route", "context", "--profile", str(profile)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "demo" in out
    assert "Do the thing" in out
    assert "run1" in out and "run2" in out


def test_context_with_work_item_missing_frontmatter_still_exits_zero_and_not_ready(tmp_path, capsys):
    profile = _write_workspace(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "work" / "demo" / "1-build" / "task.md").write_text("Just some task notes, no frontmatter at all.\n")
    rc = main(["route", "context", "--profile", str(profile), "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert doc["initiatives"] == []


_LOG_RUN3 = """
  quarantined task: c — timeout
  reused d from run-9 (approved patch, no model call)
epic run-3: 1 phase(s) complete, 0 partial, 1 blocked, 1 task(s) quarantined, 0 stack(s) rebased
  usage   : 3 node call(s), 12 turns, $0.40
"""


def _write_runs_workspace(tmp_path):
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    (ws / "runs" / "run1.pid").write_text(str(os.getpid()))  # alive: the test process itself
    (ws / "runs" / "run2.pid").write_text("999999999")  # dead: pattern from test_epic_and_cli.py
    (ws / "runs" / "run3.log").write_text(_LOG_RUN3)  # no pidfile at all
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"team: acme\nworkspace_dir: {ws}\n")
    return profile, ws


def _mtime_utc(path):
    # Independent of agent_tools.cli._mtime_iso: reimplemented here so a bug
    # in that function (say, local time instead of UTC) shows up as a
    # mismatch instead of passing on both sides of the same helper.
    import datetime

    return datetime.datetime.fromtimestamp(path.stat().st_mtime, tz=datetime.timezone.utc).isoformat()


def _expected_status_rows(ws):
    from agent_tools import epic

    entries = [
        {"id": "run1", "pid": os.getpid(), "alive": True,
         "started": _mtime_utc(ws / "runs" / "run1.pid"), **epic.summarize_log("")},
        {"id": "run2", "pid": 999999999, "alive": False,
         "started": _mtime_utc(ws / "runs" / "run2.pid"), **epic.summarize_log("")},
        {"id": "run3", "pid": None, "alive": False, "started": None, **epic.summarize_log(_LOG_RUN3)},
    ]
    return route.status_rows(entries)


def test_status_text_no_profile_exits_2_and_names_the_path(tmp_path, capsys):
    missing = tmp_path / "no-such-profile.yaml"
    rc = main(["route", "status", "--profile", str(missing)])
    out = capsys.readouterr().out
    assert rc == 2
    assert str(missing) in out


def test_status_json_no_profile_exits_2_and_names_the_path(tmp_path, capsys):
    missing = tmp_path / "no-such-profile.yaml"
    rc = main(["route", "status", "--profile", str(missing), "--json"])
    out = capsys.readouterr().out
    assert rc == 2
    assert str(missing) in out


def test_status_json_with_profile_matches_status_rows(tmp_path, capsys):
    profile, ws = _write_runs_workspace(tmp_path)
    rc = main(["route", "status", "--profile", str(profile), "--json"])
    rows = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert rows == _expected_status_rows(ws)


def test_status_text_with_profile_lists_alive_dead_and_no_pidfile_runs(tmp_path, capsys):
    profile, ws = _write_runs_workspace(tmp_path)
    rc = main(["route", "status", "--profile", str(profile)])
    out = capsys.readouterr().out
    assert rc == 0
    assert out == route.render_status(_expected_status_rows(ws)) + "\n"


def test_status_json_treats_pid_zero_as_not_alive_and_survives_an_oversized_pidfile(tmp_path, capsys):
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    (ws / "runs" / "run-zero.pid").write_text("0")  # pid 0 signals the whole process group, not a run
    (ws / "runs" / "run-huge.pid").write_text("9" * 40)  # too big for os.kill's pid_t
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"team: acme\nworkspace_dir: {ws}\n")
    rc = main(["route", "status", "--profile", str(profile), "--json"])
    rows = {row["id"]: row for row in json.loads(capsys.readouterr().out)}
    assert rc == 0
    assert rows["run-zero"]["pid"] == 0 and rows["run-zero"]["state"] == "exited"
    assert rows["run-huge"]["state"] == "exited"
