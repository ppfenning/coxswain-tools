"""`cox route lint` takes an initiative directory or an initiative id, and never passes a skipped reach check."""

from agent_tools.cli import main

MISSING = "t1 surface_missing: t1 surfaces agent_tools/missing.py, which is not in the repository"


def _world(tmp_path, monkeypatch, *, surfaces, with_repo=True):
    """An initiative `demo` under `tmp_path/ws/work`, a repo holding agent_tools/a.py, and a literal profile."""
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = tmp_path / "repo"
    (repo / "agent_tools").mkdir(parents=True)
    (repo / "agent_tools" / "a.py").write_text("x\n")
    ws = tmp_path / "ws"
    phase = ws / "work" / "demo" / "p1"
    phase.mkdir(parents=True)
    repo_line = f"repo: {repo}\n" if with_repo else ""
    (ws / "work" / "demo" / "initiative.md").write_text(f"---\nid: demo\ntitle: Demo\n{repo_line}---\n\nBody\n")
    (phase / "t1.md").write_text(
        f"---\nid: t1\ntitle: One\nphase: p1\nstate: ready\nsurfaces: [{', '.join(surfaces)}]\n---\nAdd the flag.\n",
    )
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"workspace_dir: {ws}\n")
    monkeypatch.setenv("AGENT_TOOLS_PROFILE", str(profile))
    return ws / "work" / "demo"


def test_an_id_resolves_to_its_work_directory_and_reports_a_missing_surface(tmp_path, monkeypatch, capsys):
    _world(tmp_path, monkeypatch, surfaces=["agent_tools/missing.py"])
    rc = main(["route", "lint", "demo"])
    assert rc == 2
    assert MISSING in capsys.readouterr().out


def test_a_directory_path_gives_the_same_result_without_a_profile(tmp_path, monkeypatch, capsys):
    initiative_dir = _world(tmp_path, monkeypatch, surfaces=["agent_tools/missing.py"])
    monkeypatch.delenv("AGENT_TOOLS_PROFILE")
    rc = main(["route", "lint", str(initiative_dir)])
    assert rc == 2
    assert MISSING in capsys.readouterr().out


def test_an_unknown_name_fails_and_names_both_forms(tmp_path, monkeypatch, capsys):
    _world(tmp_path, monkeypatch, surfaces=["agent_tools/a.py"])
    rc = main(["route", "lint", "nope"])
    out = capsys.readouterr().out
    assert rc != 0
    assert "directory" in out
    assert "initiative id" in out


def test_a_skipped_reach_check_with_surfaces_fails(tmp_path, monkeypatch, capsys):
    _world(tmp_path, monkeypatch, surfaces=["agent_tools/a.py"], with_repo=False)
    rc = main(["route", "lint", "demo"])
    assert rc != 0
    assert "reach check skipped" in capsys.readouterr().out


def test_a_skipped_reach_check_with_no_surfaces_may_pass(tmp_path, monkeypatch, capsys):
    _world(tmp_path, monkeypatch, surfaces=[], with_repo=False)
    rc = main(["route", "lint", "demo"])
    assert rc == 0
    assert "reach check skipped" in capsys.readouterr().out
