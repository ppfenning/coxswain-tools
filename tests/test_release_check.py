import json

from agent_tools import cli, release_check
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


def test_cli_release_check_with_a_valid_manifest_exits_zero_and_reports_no_drift(tmp_path, capsys):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text('[coxswain]\nversion = "0.1.0"\n')
    rc = cli.main(["dev", "release-check", "--root", str(tmp_path), "--manifest", str(manifest_path)])
    assert rc == 0
    assert "no checks registered" in capsys.readouterr().out


def test_cli_release_check_renders_a_drift_from_a_registered_check(tmp_path, capsys, monkeypatch):
    def stub(facts):
        return [Drift("stub", "a", 1, "b", 2, "fix it")]

    monkeypatch.setattr(release_check, "CHECKS", (stub,))
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


def test_cli_release_check_json_flag_prints_a_json_list(tmp_path, capsys):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text('[coxswain]\nversion = "0.1.0"\n')
    rc = cli.main(["dev", "release-check", "--root", str(tmp_path), "--manifest", str(manifest_path), "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {"checks_run": 0, "drifts": []}
