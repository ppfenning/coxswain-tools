import json
import os

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
