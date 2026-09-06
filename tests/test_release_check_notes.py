from agent_tools import release_check, release_check_notes
from agent_tools.release_check import Drift
from agent_tools.release_check_notes import (
    bullets_from_notes,
    check_notes,
    landed_from_gh,
    landed_from_git,
    parse_bullet,
)


def test_check_notes_resolves_against_facts_plans_own_component_dirs_and_release_notes_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(release_check_notes.shutil, "which", lambda name: "/usr/bin/gh")
    (tmp_path / "cox").mkdir()
    notes_dir = tmp_path / "coxswain" / "docs" / "releases"
    notes_dir.mkdir(parents=True)
    (notes_dir / "0.1.0.md").write_text("- cox: added retry (#42)\n")
    manifest = {"coxswain": {"version": "0.1.0"}, "components": {"cox": {"repo": "x"}}}

    def fake_run(cmd, cwd, capture_output, text):
        stdout = "" if cmd[0] == "git" else '[{"number": 42}]'
        return type("Result", (), {"stdout": stdout})()

    facts = release_check.facts_plan(str(tmp_path), manifest) | release_check_notes.gather_notes_facts(
        str(tmp_path), manifest, fake_run
    )
    assert check_notes(facts) == []


def test_parse_bullet_finds_a_prefixed_component_and_a_pr_citation():
    assert parse_bullet("- cox: fixed the thing (#42)", {"cox", "route"}) == ("cox", {"42"})


def test_parse_bullet_finds_a_component_named_anywhere_in_the_text():
    assert parse_bullet("Fixed retries in cox (#42)", {"cox", "route"}) == ("cox", {"42"})


def test_parse_bullet_finds_a_sha_citation():
    assert parse_bullet("- route: bugfix abc1234", {"cox", "route"}) == ("route", {"abc1234"})


def test_parse_bullet_names_no_component_when_none_is_known():
    assert parse_bullet("- something happened (#1)", {"cox"}) == (None, {"1"})


def test_bullets_from_notes_finds_bullets_and_skips_separators_and_flags():
    text = "# Title\n---\n- cox: added retry (#42)\n* route: fixed bug (abc1234)\n--verbose\n"
    assert bullets_from_notes(text) == [(3, "- cox: added retry (#42)"), (4, "* route: fixed bug (abc1234)")]


def test_check_notes_drifts_on_a_bullet_with_no_component():
    facts = {"component_dirs": {"cox": "/repo/cox"}, "landed": {"cox": {"42"}}, "release_notes": "notes.md",
              "notes_bullets": [(3, "- something happened (#42)")]}
    assert check_notes(facts) == [
        Drift("notes_citation", "notes.md", 3, "notes.md", None, "name a landed component for this bullet")
    ]


def test_check_notes_drifts_on_a_bullet_with_no_citation():
    facts = {"component_dirs": {"cox": "/repo/cox"}, "landed": {"cox": {"42"}}, "release_notes": "notes.md",
              "notes_bullets": [(4, "- cox: refactored internals")]}
    assert check_notes(facts) == [
        Drift("notes_citation", "notes.md", 4, "/repo/cox", None, "cite the PR or commit landed in cox")
    ]


def test_check_notes_drifts_on_a_citation_absent_from_that_components_history():
    facts = {"component_dirs": {"cox": "/repo/cox"}, "landed": {"cox": {"42"}}, "release_notes": "notes.md",
              "notes_bullets": [(5, "- cox: added retry (#99)")]}
    assert check_notes(facts) == [
        Drift("notes_citation", "notes.md", 5, "/repo/cox", None, "cite a PR or commit landed in cox, or remove")
    ]


def test_check_notes_has_no_drift_when_the_pr_citation_resolves():
    facts = {"component_dirs": {"cox": "/repo/cox"}, "landed": {"cox": {"42"}}, "release_notes": "notes.md",
              "notes_bullets": [(6, "- cox: added retry (#42)")]}
    assert check_notes(facts) == []


def test_check_notes_has_no_drift_when_a_full_sha_resolves_against_an_abbreviated_one():
    facts = {"component_dirs": {"cox": "/repo/cox"}, "landed": {"cox": {"abc1234"}}, "release_notes": "notes.md",
              "notes_bullets": [(7, "- cox: added retry abc1234567890")]}
    assert check_notes(facts) == []


def test_landed_from_git_parses_short_shas_out_of_oneline_log():
    assert landed_from_git("abc1234 fix bug\ndef5678 add feature") == {"abc1234", "def5678"}


def test_landed_from_gh_parses_pr_numbers_out_of_json():
    assert landed_from_gh('[{"number": 42}, {"number": 7}]') == {"42", "7"}


def test_landed_from_gh_returns_empty_set_on_non_json_output():
    assert landed_from_gh("gh: A new release of gh is available") == set()


def test_an_unmeasured_pr_citation_is_not_a_drift():
    facts = {"component_dirs": {"tools": "/r/tools"}, "landed": {"tools": set()}, "pr_numbers_measured": {"tools": False},
             "release_notes": "notes.md", "notes_bullets": [(3, "- tools: the gate (#59)")]}
    assert check_notes(facts) == []
