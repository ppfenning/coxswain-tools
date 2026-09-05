import datetime
import json
import os
import subprocess
import sys
import time

import pytest

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
    assert "runs: 1 in flight" in out
    assert "run1" in out and "run2" not in out


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


def _write_file_profile(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "team: acme\n"
        f"workspace_dir: {ws}\n"
        "harness_dir: /opt/agent-graphs\n"
        "cartridges_dir: /opt/cartridges\n"
        "provider_profile: /opt/providers/acme.yaml\n"
    )
    return profile, ws


def test_file_writes_initiative_files_at_expected_paths(tmp_path, capsys):
    profile, ws = _write_file_profile(tmp_path)
    rc = main(["route", "file", "--profile", str(profile), "--repo", "/repos/widget", "--title", "Fix the thing"])
    out = capsys.readouterr().out
    assert rc == 0
    expected = route.initiative_files("Fix the thing", "", "/repos/widget")
    for rel, text in expected.items():
        assert (ws / rel).read_text(encoding="utf-8") == text
        assert str(ws / rel) in out


def test_file_intake_writes_the_intake_file_with_todays_date(tmp_path, capsys):
    profile, ws = _write_file_profile(tmp_path)
    expected_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    rc = main(["route", "file", "--profile", str(profile), "--repo", "/repos/widget", "--title", "Fix the thing", "--intake"])
    out = capsys.readouterr().out
    assert rc == 0
    written = list((ws / "intake").glob("*.md"))
    assert len(written) == 1
    assert written[0].name.startswith(expected_date)
    expected = route.intake_file("Fix the thing", "", "/repos/widget", expected_date)
    [(rel, text)] = expected.items()
    assert (ws / rel).read_text(encoding="utf-8") == text
    assert str(ws / rel) in out


def test_file_refuses_an_existing_path_collision(tmp_path, capsys):
    profile, ws = _write_file_profile(tmp_path)
    slug = route.slugify("Fix the thing")
    target = ws / "work" / slug / "initiative.md"
    target.parent.mkdir(parents=True)
    target.write_text("already here\n")
    rc = main(["route", "file", "--profile", str(profile), "--repo", "/repos/widget", "--title", "Fix the thing"])
    out = capsys.readouterr().out
    assert rc == 2
    assert str(target) in out
    assert target.read_text() == "already here\n"


def test_file_refuses_a_missing_profile(tmp_path, capsys):
    missing = tmp_path / "no-such-profile.yaml"
    rc = main(["route", "file", "--profile", str(missing), "--repo", "/repos/widget", "--title", "Fix the thing"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "no profile" in out


def _write_harness(tmp_path):
    harness_dir = tmp_path / "harness"
    (harness_dir / ".venv" / "bin").mkdir(parents=True)
    (harness_dir / ".venv" / "bin" / "python").symlink_to(sys.executable)
    (harness_dir / "shell.py").write_text(
        "import sys, json\n"
        "from pathlib import Path\n"
        "Path(__file__).with_name('recorded_argv.json').write_text(json.dumps(sys.argv))\n"
    )
    return harness_dir


def _wait_for(path, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    return path.exists()


def _write_launch_profile(tmp_path, harness_dir, ws):
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "team: acme\n"
        f"harness_dir: {harness_dir}\n"
        f"workspace_dir: {ws}\n"
        "cartridges_dir: /opt/cartridges\n"
        "provider_profile: /opt/providers/acme.yaml\n"
    )
    return profile


def _init_repo(repo):
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "a"], cwd=repo, check=True)
    (repo / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)


def test_launch_epic_starts_the_harness_detached_with_the_recorded_argv(tmp_path, capsys):
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    repo = tmp_path / "repo"
    _init_repo(repo)
    initiative_dir = ws / "work" / "demo"
    initiative_dir.mkdir(parents=True)
    (initiative_dir / "initiative.md").write_text("---\nid: demo\ntitle: Demo\n---\n\nBody\n")
    profile = _write_launch_profile(tmp_path, harness_dir, ws)

    rc = main([
        "route", "launch", "epic",
        "--profile", str(profile),
        "--initiative", str(initiative_dir),
        "--repo", str(repo),
        "--fix-attempts", "3",
    ])
    assert rc == 0
    pid_path = ws / "runs" / "demo-1.pid"
    log_path = ws / "runs" / "demo-1.log"
    assert _wait_for(pid_path)
    assert pid_path.read_text().strip().isdigit()
    assert log_path.exists()
    recorded = harness_dir / "recorded_argv.json"
    assert _wait_for(recorded)
    argv = json.loads(recorded.read_text())
    expected = route.harness_argv(
        route.parse_profile(profile.read_text()), "epic", "demo-1",
        initiative=str(initiative_dir), repo=str(repo), fix_attempts=3,
    )
    assert argv == expected[1:]
    assert "--fix-attempts" in argv and "3" in argv


def test_launch_decompose_starts_the_harness_detached_with_the_recorded_argv(tmp_path, capsys):
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    (ws / "intake").mkdir(parents=True)
    idea = ws / "intake" / "2026-09-04-fix-thing.md"
    idea.write_text("---\nid: fix-thing\ntitle: Fix thing\n---\n\nBody\n")
    profile = _write_launch_profile(tmp_path, harness_dir, ws)

    rc = main([
        "route", "launch", "decompose",
        "--profile", str(profile),
        "--idea", str(idea),
        "--initiative-id", "fix-thing",
    ])
    assert rc == 0
    pid_path = ws / "runs" / "fix-thing-1.pid"
    assert _wait_for(pid_path)
    recorded = harness_dir / "recorded_argv.json"
    assert _wait_for(recorded)
    argv = json.loads(recorded.read_text())
    expected = route.harness_argv(
        route.parse_profile(profile.read_text()), "decompose", "fix-thing-1",
        idea=str(idea), initiative_id="fix-thing",
    )
    assert argv == expected[1:]


def test_launch_dry_run_prints_argv_and_starts_nothing(tmp_path, capsys):
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    (ws / "intake").mkdir(parents=True)
    idea = ws / "intake" / "idea.md"
    idea.write_text("body\n")
    profile = _write_launch_profile(tmp_path, harness_dir, ws)

    rc = main([
        "route", "launch", "decompose", "--dry-run",
        "--profile", str(profile),
        "--idea", str(idea),
        "--initiative-id", "fix-thing",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "fix-thing-1" in out
    assert f"trace {ws / 'runs' / 'fix-thing-1-trace'}" in out
    assert not (ws / "runs" / "fix-thing-1.pid").exists()
    assert not (ws / "runs" / "fix-thing-1.log").exists()
    assert not (harness_dir / "recorded_argv.json").exists()


def test_launch_refuses_a_missing_profile(tmp_path, capsys):
    missing = tmp_path / "no-such-profile.yaml"
    rc = main(["route", "launch", "epic", "--profile", str(missing), "--initiative", str(tmp_path / "x")])
    out = capsys.readouterr().out
    assert rc == 2
    assert "no profile" in out


def test_launch_refuses_a_bad_graph_name(tmp_path, capsys):
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    profile = _write_launch_profile(tmp_path, harness_dir, ws)
    with pytest.raises(SystemExit) as exc:
        main(["route", "launch", "bogus", "--profile", str(profile)])
    assert exc.value.code == 2
    assert list((ws / "runs").iterdir()) == []


def test_launch_refuses_when_the_harness_venv_is_missing(tmp_path, capsys):
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    harness_dir = tmp_path / "harness"  # no .venv/bin/python, no shell.py
    profile = _write_launch_profile(tmp_path, harness_dir, ws)
    rc = main(["route", "launch", "epic", "--profile", str(profile), "--initiative", str(tmp_path / "x")])
    out = capsys.readouterr().out
    assert rc == 2
    assert "harness venv missing" in out


def test_launch_epic_refuses_when_initiative_md_is_missing(tmp_path, capsys):
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    initiative_dir = ws / "work" / "demo"
    initiative_dir.mkdir(parents=True)
    profile = _write_launch_profile(tmp_path, harness_dir, ws)
    rc = main(["route", "launch", "epic", "--profile", str(profile), "--initiative", str(initiative_dir)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "no initiative.md" in out


def test_launch_epic_refuses_when_the_repo_is_unresolvable(tmp_path, capsys):
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    initiative_dir = ws / "work" / "demo"
    initiative_dir.mkdir(parents=True)
    (initiative_dir / "initiative.md").write_text("---\nid: demo\ntitle: Demo\n---\n\nBody\n")
    profile = _write_launch_profile(tmp_path, harness_dir, ws)
    rc = main(["route", "launch", "epic", "--profile", str(profile), "--initiative", str(initiative_dir)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "no --repo" in out


def test_launch_epic_refuses_when_the_repo_is_dirty(tmp_path, capsys):
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "dirty.txt").write_text("uncommitted\n")
    initiative_dir = ws / "work" / "demo"
    initiative_dir.mkdir(parents=True)
    (initiative_dir / "initiative.md").write_text("---\nid: demo\ntitle: Demo\n---\n\nBody\n")
    profile = _write_launch_profile(tmp_path, harness_dir, ws)
    rc = main(["route", "launch", "epic", "--profile", str(profile), "--initiative", str(initiative_dir), "--repo", str(repo)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "uncommitted changes" in out


def test_launch_epic_refuses_when_the_initiative_already_has_a_live_run(tmp_path, capsys):
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    repo = tmp_path / "repo"
    _init_repo(repo)
    initiative_dir = ws / "work" / "demo"
    initiative_dir.mkdir(parents=True)
    (initiative_dir / "initiative.md").write_text("---\nid: demo\ntitle: Demo\n---\n\nBody\n")
    (ws / "runs" / "demo-1.pid").write_text(str(os.getpid()))  # alive: the test process itself
    profile = _write_launch_profile(tmp_path, harness_dir, ws)
    rc = main(["route", "launch", "epic", "--profile", str(profile), "--initiative", str(initiative_dir), "--repo", str(repo)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "already running" in out


def test_launch_decompose_refuses_when_the_idea_file_is_missing(tmp_path, capsys):
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    profile = _write_launch_profile(tmp_path, harness_dir, ws)
    rc = main(["route", "launch", "decompose", "--profile", str(profile), "--idea", str(tmp_path / "no.md"), "--initiative-id", "x"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "no idea file" in out


def test_launch_refuses_a_profile_missing_a_key_harness_argv_needs(tmp_path, capsys):
    ws = tmp_path / "workspace"
    ws.mkdir()
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"team: acme\nworkspace_dir: {ws}\n")  # no harness_dir, cartridges_dir, provider_profile
    rc = main(["route", "launch", "epic", "--profile", str(profile), "--initiative", str(tmp_path / "x")])
    out = capsys.readouterr().out
    assert rc == 2
    assert "harness_dir not set" in out
