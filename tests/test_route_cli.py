import datetime
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from agent_tools import leader, pacing, route, usage_window
from agent_tools.cli import main
from agent_tools.pacing import Window

# Fixed in the past so `_usage_assessment`'s own `datetime.now(UTC)` always
# falls after `_USAGE_END`, pinning `elapsed_fraction` regardless of when the
# test runs (same trick as tests/test_usage_assess_cli.py).
_USAGE_START = datetime.datetime(2020, 1, 1, 0, 0, 0, tzinfo=datetime.UTC)
_USAGE_END = datetime.datetime(2020, 1, 1, 10, 0, 0, tzinfo=datetime.UTC)


def _unmeasured_window(*_a, **_k) -> Window:
    return Window(start=_USAGE_START, end=_USAGE_END, spent_usd=0.0, ceiling_usd=None,
                  burn_usd_per_hour=0.0, runs_in_flight=0)


@pytest.fixture(autouse=True)
def _stub_usage_gather(monkeypatch):
    """Every route context/launch test in this file crosses `_usage_assessment`;
    stub the gatherer to an unmeasured window so no test shells out to `npx
    ccusage`. A test that wants a different verdict re-patches `gather` itself."""
    monkeypatch.setattr(usage_window, "gather", _unmeasured_window)


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
    assert doc["intake"] == {
        "queued": [{"id": "i1", "title": "Do the thing", "initiative": None, "done": False, "path": "intake/one.md"}],
        "decomposed": [],
        "landed": [],
    }


def test_context_text_with_full_profile_lists_initiative_and_runs(tmp_path, capsys):
    profile = _write_workspace(tmp_path)
    rc = main(["route", "context", "--profile", str(profile)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "demo" in out
    assert "intake: 1 queued, 0 decomposed, 0 landed" in out
    assert "runs: 1 in flight" in out
    assert "run1" in out and "run2" not in out


def test_context_text_with_full_profile_ends_with_the_usage_reason_line(tmp_path, capsys):
    profile = _write_workspace(tmp_path)
    rc = main(["route", "context", "--profile", str(profile)])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines()[-1] == "usage: window is unmeasured: no usable ceiling_usd; reporting pace only"


def test_context_json_with_full_profile_carries_the_same_usage_reason(tmp_path, capsys):
    profile = _write_workspace(tmp_path)
    rc = main(["route", "context", "--profile", str(profile), "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert doc["usage"] == "window is unmeasured: no usable ceiling_usd; reporting pace only"


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

    return datetime.datetime.fromtimestamp(path.stat().st_mtime, tz=datetime.UTC).isoformat()


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


def test_status_json_with_an_intake_dir_carries_runs_and_intake_groups(tmp_path, capsys):
    profile, ws = _write_runs_workspace(tmp_path)
    (ws / "intake").mkdir()
    (ws / "intake" / "2026-09-01-q.md").write_text("---\nid: q\ntitle: Q\n---\n\nBody.\n")
    rc = main(["route", "status", "--profile", str(profile), "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert doc["runs"] == _expected_status_rows(ws)
    assert doc["intake"] == {
        "queued": [{"id": "q", "title": "Q", "initiative": None, "done": False, "path": "intake/2026-09-01-q.md"}],
        "decomposed": [],
        "landed": [],
    }


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
    expected_date = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
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


def test_file_from_intake_links_both_ways_retires_the_file_and_status_reports_decomposed(tmp_path, capsys):
    profile, ws = _write_file_profile(tmp_path)
    (ws / "intake").mkdir()
    intake_path = ws / "intake" / "2026-09-01-fix-thing.md"
    intake_path.write_text("---\nid: fix-thing\ntitle: Fix the thing\nrepo: /repos/widget\n---\n\nDo it.\n")
    rc = main(["route", "file", "--profile", str(profile), "--from-intake", str(intake_path)])
    capsys.readouterr()
    assert rc == 0
    assert not intake_path.exists()
    done_path = ws / "intake" / "done" / "2026-09-01-fix-thing.md"
    assert done_path.exists()
    assert route.parse_frontmatter(done_path.read_text())[0]["initiative"] == route.slugify("Fix the thing")
    slug = route.slugify("Fix the thing")
    initiative_text = (ws / "work" / slug / "initiative.md").read_text()
    assert route.parse_frontmatter(initiative_text)[0]["intake"] == "intake/2026-09-01-fix-thing.md"
    rc = main(["route", "status", "--profile", str(profile)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "intake decomposed: 1" in out


def test_file_from_intake_refuses_a_path_outside_the_workspace_intake_dir(tmp_path, capsys):
    profile, ws = _write_file_profile(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("---\nid: x\ntitle: X\nrepo: /repos/widget\n---\n\nBody.\n")
    rc = main(["route", "file", "--profile", str(profile), "--from-intake", str(outside)])
    out = capsys.readouterr().out
    assert rc == 2
    assert outside.exists()
    assert "must name a file directly under" in out


def test_status_reports_landed_once_the_linked_initiatives_one_task_is_done(tmp_path, capsys):
    profile, ws = _write_file_profile(tmp_path)
    (ws / "intake").mkdir()
    intake_path = ws / "intake" / "2026-09-01-fix-thing.md"
    intake_path.write_text("---\nid: fix-thing\ntitle: Fix the thing\nrepo: /repos/widget\n---\n\nDo it.\n")
    rc = main(["route", "file", "--profile", str(profile), "--from-intake", str(intake_path)])
    capsys.readouterr()
    assert rc == 0
    slug = route.slugify("Fix the thing")
    task_path = ws / "work" / slug / "build" / f"{slug}.md"
    text = task_path.read_text()
    task_path.write_text(text.replace("state: ready", "state: done"))
    rc = main(["route", "status", "--profile", str(profile)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "intake landed: 1" in out


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


def _write_launch_profile(tmp_path, harness_dir, ws, provider_profile=None):
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "team: acme\n"
        f"harness_dir: {harness_dir}\n"
        f"workspace_dir: {ws}\n"
        "cartridges_dir: /opt/cartridges\n"
        f"provider_profile: {provider_profile or '/opt/providers/acme.yaml'}\n"
    )
    return profile


def _write_provider_profile(tmp_path, tier="deep", effort="high"):
    """A provider profile in its real shape: `tiers` maps each tier the profile
    offers (up to `tier`) to a model, `effort` maps each to `effort`."""
    models = {"cheap": "haiku", "standard": "sonnet", "deep": "opus"}
    offered = ["cheap", "standard", "deep"][: ["cheap", "standard", "deep"].index(tier) + 1]
    tiers = "".join(f"  {name}: {models[name]}\n" for name in offered)
    efforts = "".join(f"  {name}: {effort}\n" for name in offered)
    provider = tmp_path / "provider.yaml"
    provider.write_text(f"command: claude\ntiers:\n{tiers}effort:\n{efforts}")
    return provider


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


def test_launch_writes_launched_json_naming_the_lock_holder(tmp_path, capsys):
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    (ws / "intake").mkdir(parents=True)
    idea = ws / "intake" / "idea.md"
    idea.write_text("---\nid: fix-thing\ntitle: Fix thing\n---\n\nBody\n")
    profile = _write_launch_profile(tmp_path, harness_dir, ws)
    fresh = datetime.datetime.now(datetime.UTC).isoformat()
    leader.write(ws / "runs", {"session": "alice", "pid": 1, "host": "some-other-host", "taken_at": fresh, "heartbeat_at": fresh})

    rc = main([
        "route", "launch", "decompose", "--label", "alice",
        "--profile", str(profile),
        "--idea", str(idea),
        "--initiative-id", "fix-thing",
    ])
    assert rc == 0
    launched_path = ws / "runs" / "fix-thing-1.launched.json"
    assert _wait_for(launched_path)
    assert json.loads(launched_path.read_text())["launched_by"] == "alice"


def test_launch_with_no_held_lock_writes_launched_json_with_no_launched_by_key(tmp_path, capsys):
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    (ws / "intake").mkdir(parents=True)
    idea = ws / "intake" / "idea.md"
    idea.write_text("---\nid: fix-thing\ntitle: Fix thing\n---\n\nBody\n")
    profile = _write_launch_profile(tmp_path, harness_dir, ws)

    rc = main([
        "route", "launch", "decompose",
        "--profile", str(profile),
        "--idea", str(idea),
        "--initiative-id", "fix-thing",
    ])
    assert rc == 0
    launched_path = ws / "runs" / "fix-thing-1.launched.json"
    assert _wait_for(launched_path)
    assert "launched_by" not in json.loads(launched_path.read_text())


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


def test_launch_cos_dry_run_prints_argv_pid_log_and_trace(tmp_path, capsys):
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    profile = _write_launch_profile(tmp_path, harness_dir, ws)

    rc = main(["route", "launch", "cos", "--dry-run", "--profile", str(profile)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "dry-run:" in out and "cos" in out
    assert "pid " in out
    assert "log " in out
    assert "trace " in out
    assert not (ws / "runs" / "cos-1.pid").exists()


def test_launch_refuses_a_missing_profile(tmp_path, capsys):
    missing = tmp_path / "no-such-profile.yaml"
    rc = main(["route", "launch", "epic", "--profile", str(missing), "--initiative", str(tmp_path / "x")])
    out = capsys.readouterr().out
    assert rc == 2
    assert "no profile" in out


def test_launch_cos_refuses_a_missing_profile(tmp_path, capsys):
    missing = tmp_path / "no-such-profile.yaml"
    rc = main(["route", "launch", "cos", "--profile", str(missing)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "no profile" in out


def test_launch_cos_refuses_when_a_cos_run_is_already_live(tmp_path, capsys):
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    (ws / "runs" / "cos-1.pid").write_text(str(os.getpid()))  # alive: the test process itself
    profile = _write_launch_profile(tmp_path, harness_dir, ws)
    rc = main(["route", "launch", "cos", "--profile", str(profile)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "already running" in out


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


def test_launch_refuses_a_tier_ceiling_above_the_profiles_own_ceiling(tmp_path, capsys):
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    (ws / "intake").mkdir(parents=True)
    idea = ws / "intake" / "idea.md"
    idea.write_text("body\n")
    provider = _write_provider_profile(tmp_path, tier="standard", effort="high")
    profile = _write_launch_profile(tmp_path, harness_dir, ws, provider_profile=provider)

    rc = main([
        "route", "launch", "decompose",
        "--profile", str(profile),
        "--idea", str(idea),
        "--initiative-id", "fix-thing",
        "--tier-ceiling", "deep",
    ])
    out = capsys.readouterr().out
    assert rc == 2
    assert "standard" in out and "deep" in out
    assert not (ws / "runs" / "fix-thing-1.pid").exists()
    assert not list((ws / "runs").glob("*.ceiling.json"))


def test_launch_epic_with_a_tightening_ceiling_writes_the_overlaid_profile_and_ceiling_record(tmp_path, capsys):
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    repo = tmp_path / "repo"
    _init_repo(repo)
    initiative_dir = ws / "work" / "demo"
    initiative_dir.mkdir(parents=True)
    (initiative_dir / "initiative.md").write_text("---\nid: demo\ntitle: Demo\n---\n\nBody\n")
    provider = _write_provider_profile(tmp_path, tier="deep", effort="high")
    profile = _write_launch_profile(tmp_path, harness_dir, ws, provider_profile=provider)

    rc = main([
        "route", "launch", "epic",
        "--profile", str(profile),
        "--initiative", str(initiative_dir),
        "--repo", str(repo),
        "--tier-ceiling", "standard",
        "--effort-ceiling", "low",
    ])
    assert rc == 0
    run_id = "demo-1"
    assert _wait_for(ws / "runs" / f"{run_id}.pid")
    ceiling_path = ws / "runs" / f"{run_id}.ceiling.json"
    assert ceiling_path.exists()
    record = json.loads(ceiling_path.read_text())
    assert record["requested"] == {"tier": "standard", "effort": "low"}
    assert record["applied"] == {"tier": "standard", "effort": "low"}
    overlaid_path = Path(record["profile"])
    overlaid = yaml.safe_load(overlaid_path.read_text())
    assert overlaid["tiers"] == {"cheap": "haiku", "standard": "sonnet", "deep": "sonnet"}
    assert overlaid["effort"] == {"cheap": "low", "standard": "low", "deep": "low"}
    recorded = harness_dir / "recorded_argv.json"
    assert _wait_for(recorded)
    argv = json.loads(recorded.read_text())
    assert argv[argv.index("--provider-profile") + 1] == str(overlaid_path)


def test_launch_with_no_ceiling_flags_writes_no_ceiling_record(tmp_path, capsys):
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    (ws / "intake").mkdir(parents=True)
    idea = ws / "intake" / "idea.md"
    idea.write_text("body\n")
    provider = _write_provider_profile(tmp_path, tier="deep", effort="high")
    profile = _write_launch_profile(tmp_path, harness_dir, ws, provider_profile=provider)

    rc = main([
        "route", "launch", "decompose",
        "--profile", str(profile),
        "--idea", str(idea),
        "--initiative-id", "fix-thing",
    ])
    assert rc == 0
    assert _wait_for(ws / "runs" / "fix-thing-1.pid")
    assert not list((ws / "runs").glob("*.ceiling.json"))
    recorded = harness_dir / "recorded_argv.json"
    assert _wait_for(recorded)
    argv = json.loads(recorded.read_text())
    assert argv[argv.index("--provider-profile") + 1] == str(provider)


def test_launch_with_only_a_tier_ceiling_leaves_effort_unset_in_the_ceiling_record(tmp_path, capsys):
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    (ws / "intake").mkdir(parents=True)
    idea = ws / "intake" / "idea.md"
    idea.write_text("body\n")
    provider = _write_provider_profile(tmp_path, tier="deep", effort="high")
    profile = _write_launch_profile(tmp_path, harness_dir, ws, provider_profile=provider)

    rc = main([
        "route", "launch", "decompose",
        "--profile", str(profile),
        "--idea", str(idea),
        "--initiative-id", "fix-thing",
        "--tier-ceiling", "standard",
    ])
    assert rc == 0
    ceiling_path = ws / "runs" / "fix-thing-1.ceiling.json"
    record = json.loads(ceiling_path.read_text())
    assert record["requested"] == {"tier": "standard", "effort": None}
    assert record["applied"] == {"tier": "standard", "effort": None}


def test_launch_dry_run_with_a_ceiling_writes_no_provider_profile_or_ceiling_file(tmp_path, capsys):
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    (ws / "intake").mkdir(parents=True)
    idea = ws / "intake" / "idea.md"
    idea.write_text("body\n")
    provider = _write_provider_profile(tmp_path, tier="deep", effort="high")
    profile = _write_launch_profile(tmp_path, harness_dir, ws, provider_profile=provider)

    rc = main([
        "route", "launch", "decompose", "--dry-run",
        "--profile", str(profile),
        "--idea", str(idea),
        "--initiative-id", "fix-thing",
        "--tier-ceiling", "standard",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "fix-thing-1.provider-profile.yaml" in out
    assert not (ws / "runs" / "fix-thing-1.provider-profile.yaml").exists()
    assert not (ws / "runs" / "fix-thing-1.ceiling.json").exists()
    assert not (ws / "runs" / "fix-thing-1.pid").exists()


def test_launch_refuses_when_the_provider_profile_is_missing_and_a_ceiling_is_requested(tmp_path, capsys):
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    (ws / "intake").mkdir(parents=True)
    idea = ws / "intake" / "idea.md"
    idea.write_text("body\n")
    profile = _write_launch_profile(tmp_path, harness_dir, ws)  # default provider_profile path does not exist

    rc = main([
        "route", "launch", "decompose",
        "--profile", str(profile),
        "--idea", str(idea),
        "--initiative-id", "fix-thing",
        "--tier-ceiling", "standard",
    ])
    out = capsys.readouterr().out
    assert rc == 2
    assert "provider profile" in out
    assert not (ws / "runs" / "fix-thing-1.pid").exists()


def test_launch_refuses_when_the_provider_profile_is_not_valid_yaml(tmp_path, capsys):
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    (ws / "intake").mkdir(parents=True)
    idea = ws / "intake" / "idea.md"
    idea.write_text("body\n")
    provider = tmp_path / "broken.yaml"
    provider.write_text("tier: [unterminated\n")
    profile = _write_launch_profile(tmp_path, harness_dir, ws, provider_profile=provider)

    rc = main([
        "route", "launch", "decompose",
        "--profile", str(profile),
        "--idea", str(idea),
        "--initiative-id", "fix-thing",
        "--tier-ceiling", "standard",
    ])
    out = capsys.readouterr().out
    assert rc == 2
    assert "not valid YAML" in out
    assert not (ws / "runs" / "fix-thing-1.pid").exists()


def _stopped_window(*_a, **_k) -> Window:
    return Window(start=_USAGE_START, end=_USAGE_END, spent_usd=95.0, ceiling_usd=10.0,
                  burn_usd_per_hour=90.0, runs_in_flight=3)


def test_launch_refuses_before_starting_when_usage_verdict_is_stop(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(usage_window, "gather", _stopped_window)
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    profile = _write_launch_profile(tmp_path, harness_dir, ws)

    rc = main(["route", "launch", "cos", "--dry-run", "--profile", str(profile)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "usage stop" in out
    assert "both ladders exhausted" in out
    assert not (ws / "runs" / "cos-1.pid").exists()


def test_launch_with_force_overrides_a_usage_stop_and_continues(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(usage_window, "gather", _stopped_window)
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    profile = _write_launch_profile(tmp_path, harness_dir, ws)

    rc = main(["route", "launch", "cos", "--dry-run", "--force", "--profile", str(profile)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "overridden by --force" in out
    assert "dry-run:" in out and "cos" in out


def test_gather_to_assess_seam_survives_an_aware_now_with_no_stub_on_gather():
    """Regression for the naive-clock bug: `window_from` builds an aware
    window from ccusage's own ISO timestamps, and `assess` must accept an
    aware `now` without `gather` being stubbed at all."""
    blocks_json = {
        "blocks": [{
            "isActive": True,
            "startTime": "2024-01-01T00:00:00Z",
            "endTime": "2024-01-01T05:00:00Z",
            "costUSD": 5.0,
        }]
    }
    now = datetime.datetime(2024, 1, 1, 5, 0, 0, tzinfo=datetime.UTC)
    window = usage_window.window_from(blocks_json, [], now)
    result = pacing.assess(window, usage_window.DEFAULT_POLICY, now)
    assert result.verdict == "go"
    assert result.reason == "window is unmeasured: no usable ceiling_usd; reporting pace only"


def test_launch_refuses_when_the_provider_profile_does_not_parse_to_a_mapping(tmp_path, capsys):
    harness_dir = _write_harness(tmp_path)
    ws = tmp_path / "workspace"
    (ws / "runs").mkdir(parents=True)
    (ws / "intake").mkdir(parents=True)
    idea = ws / "intake" / "idea.md"
    idea.write_text("body\n")
    provider = tmp_path / "list.yaml"
    provider.write_text("- a\n- b\n")
    profile = _write_launch_profile(tmp_path, harness_dir, ws, provider_profile=provider)

    rc = main([
        "route", "launch", "decompose",
        "--profile", str(profile),
        "--idea", str(idea),
        "--initiative-id", "fix-thing",
        "--tier-ceiling", "standard",
    ])
    out = capsys.readouterr().out
    assert rc == 2
    assert "provider profile" in out
    assert not (ws / "runs" / "fix-thing-1.pid").exists()
