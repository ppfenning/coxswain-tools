import json

from agent_tools import cli, release_check, release_check_cli, release_check_manifest, release_check_notes
from agent_tools.release_check import Drift, facts_plan, render, run_checks, to_json


def test_run_checks_concatenates_every_check_over_the_same_facts():
    def a(facts):
        return [Drift("a", "x", 1, "y", None, "fix a")]

    def b(facts):
        return [Drift("b", "p", None, "q", 2, "fix b")]

    assert run_checks({}, (a, b)) == [
        Drift("a", "x", 1, "y", None, "fix a"),
        Drift("b", "p", None, "q", 2, "fix b"),
    ]


def test_render_of_one_drift_shows_both_sides_and_the_correction():
    d = Drift("manifest", "manifest.toml", 3, "docs/components/cox.md", None, "bump docs to 0.2.0")
    assert render([d], 1) == "manifest: manifest.toml:3 <-> docs/components/cox.md — bump docs to 0.2.0"


def test_render_of_no_drifts_says_so():
    assert render([], 0) == "no checks registered: nothing measured"
    assert render([], 2) == "no drift (2 checks)"


def test_to_json_of_one_drift_is_a_plain_dict():
    d = Drift("cli_surface", "cli.py", 10, "README.md", 20, "add cox dev release-check")
    assert to_json([d]) == [{
        "check": "cli_surface", "a_file": "cli.py", "a_line": 10,
        "b_file": "README.md", "b_line": 20, "correction": "add cox dev release-check",
    }]


def test_facts_plan_names_the_umbrella_component_dirs_and_docs_paths():
    manifest = {"coxswain": {"version": "0.2.0"}, "components": {"cox": {"repo": "x"}}}
    facts = facts_plan("/root", manifest)
    assert facts["umbrella"] == "/root/coxswain"
    assert facts["component_dirs"] == {"cox": "/root/cox"}
    assert facts["component_docs"] == {"cox": "/root/coxswain/docs/components/cox.md"}
    assert facts["release_notes"] == "/root/coxswain/docs/releases/0.2.0.md"
    assert facts["readmes"] == {"cox": "/root/cox/README.md"}


def test_cli_release_check_with_a_component_missing_its_docs_finds_the_manifest_drift(tmp_path, capsys):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text('[coxswain]\nversion = "0.1.0"\n[components.cox]\ntag = "v0.1.0"\n')
    rc = cli.main(["dev", "release-check", "--root", str(tmp_path), "--manifest", str(manifest_path)])
    out = capsys.readouterr().out
    assert rc == 0
    page_path = tmp_path / "coxswain" / "docs" / "components" / "cox.md"
    notes_path = tmp_path / "coxswain" / "docs" / "releases" / "0.1.0.md"
    assert f"add {page_path} for cox" in out
    assert f"add {notes_path}" in out


def test_check_versions_is_silent_when_manifest_component_and_umbrella_all_agree():
    facts = {
        "expected_version": "0.2.0",
        "umbrella_pyproject": {"project": {"version": "0.2.0"}},
        "component_pyprojects": {"cox": {"project": {"version": "0.2.0"}}},
    }
    assert release_check.check_versions(facts) == []


def test_check_versions_drifts_on_a_component_pyproject_below_the_manifest_version():
    facts = {
        "expected_version": "0.2.0",
        "manifest_path": "manifest.toml",
        "umbrella_pyproject": {"project": {"version": "0.2.0"}},
        "component_pyprojects": {"cox": {"project": {"version": "0.1.0"}}},
        "pyprojects": {"cox": "/root/cox/pyproject.toml"},
    }
    drifts = release_check.check_versions(facts)
    assert len(drifts) == 1
    d = drifts[0]
    assert d.check == "versions"
    assert d.b_file == "/root/cox/pyproject.toml"
    assert "cox" in d.correction and "0.1.0" in d.correction and "0.2.0" in d.correction
    assert "cox dev release 0.2.0" in d.correction


def test_gather_version_facts_reads_each_component_and_the_umbrella_pyproject_off_disk(tmp_path):
    (tmp_path / "cox").mkdir()
    (tmp_path / "cox" / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    (tmp_path / "coxswain").mkdir()
    (tmp_path / "coxswain" / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n')
    manifest = {"coxswain": {"version": "0.2.0"}}
    facts = release_check.gather_version_facts(
        manifest, "manifest.toml", {"cox": str(tmp_path / "cox")}, str(tmp_path / "coxswain")
    )
    assert facts["expected_version"] == "0.2.0"
    assert facts["component_pyprojects"] == {"cox": {"project": {"version": "0.1.0"}}}
    assert facts["umbrella_pyproject"] == {"project": {"version": "0.2.0"}}


def test_cli_release_check_reports_a_real_versions_drift_from_disk(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(release_check_cli, "gather_cli_facts", lambda root, run: {})
    monkeypatch.setattr(release_check_manifest, "gather_manifest_facts", lambda *a: {})
    monkeypatch.setattr(release_check_notes, "gather_notes_facts", lambda *a, **k: {})
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text('[coxswain]\nversion = "0.2.0"\n[components.cox]\ntag = "v0.2.0"\n')
    (tmp_path / "cox").mkdir()
    (tmp_path / "cox" / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    rc = cli.main(["dev", "release-check", "--root", str(tmp_path), "--manifest", str(manifest_path), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    versions_drifts = [d for d in payload["drifts"] if d["check"] == "versions"]
    assert len(versions_drifts) == 1
    assert "cox" in versions_drifts[0]["correction"] and "cox dev release 0.2.0" in versions_drifts[0]["correction"]


def test_cli_release_check_with_a_valid_manifest_exits_zero_and_reports_no_drift(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(release_check_cli, "gather_cli_facts", lambda root, run: {})
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text('[coxswain]\nversion = "0.1.0"\n[components.cox]\ntag = "v0.1.0"\n')
    monkeypatch.setattr(release_check_manifest, "gather_manifest_facts", lambda *a: {})
    monkeypatch.setattr(release_check_notes, "gather_notes_facts", lambda *a, **k: {})
    rc = cli.main(["dev", "release-check", "--root", str(tmp_path), "--manifest", str(manifest_path)])
    assert rc == 0
    assert "no drift (6 checks)" in capsys.readouterr().out


def test_cli_release_check_renders_a_drift_from_a_registered_check(tmp_path, capsys, monkeypatch):
    def stub(facts):
        return [Drift("stub", "a", 1, "b", 2, "fix it")]

    monkeypatch.setattr(release_check, "CHECKS", (stub,))
    monkeypatch.setattr(release_check_cli, "gather_cli_facts", lambda root, run: {})
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text('[coxswain]\nversion = "0.1.0"\n')
    rc = cli.main(["dev", "release-check", "--root", str(tmp_path), "--manifest", str(manifest_path)])
    assert rc == 0
    assert "stub: a:1 <-> b:2 — fix it" in capsys.readouterr().out


def test_cli_release_check_with_a_missing_manifest_refuses_and_names_it(tmp_path, capsys):
    manifest_path = tmp_path / "manifest.toml"
    rc = cli.main(["dev", "release-check", "--root", str(tmp_path), "--manifest", str(manifest_path)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "refusing: no manifest" in out and str(manifest_path) in out
    assert "no drift" not in out


def test_cli_release_check_json_flag_prints_a_json_list(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(release_check_cli, "gather_cli_facts", lambda root, run: {})
    monkeypatch.setattr(release_check_manifest, "gather_manifest_facts", lambda *a: {})
    monkeypatch.setattr(release_check_notes, "gather_notes_facts", lambda *a, **k: {})
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text('[coxswain]\nversion = "0.1.0"\n')
    rc = cli.main(["dev", "release-check", "--root", str(tmp_path), "--manifest", str(manifest_path), "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {"checks_run": 6, "drifts": []}
