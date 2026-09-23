from agent_tools import release_check, release_check_notes
from agent_tools.release_check import Drift
from agent_tools.release_check_notes import (
    bullets_from_notes,
    check_notes,
    landed_from_git,
    parse_bullet,
    previous_version,
)


def test_check_notes_resolves_against_facts_plans_own_component_dirs_and_release_notes_keys(tmp_path):
    (tmp_path / "cox").mkdir()
    notes_dir = tmp_path / "coxswain" / "docs" / "releases"
    notes_dir.mkdir(parents=True)
    (notes_dir / "0.1.0.md").write_text("- cox: added retry (#42)\n")
    manifest = {"coxswain": {"version": "0.1.0"}, "components": {"cox": {"repo": "x"}}}

    def fake_run(cmd, cwd, capture_output, text):
        stdout = "abc1234 added retry (#42)" if cmd[1] == "log" else ""
        return type("Result", (), {"stdout": stdout})()

    facts = release_check.facts_plan(str(tmp_path), manifest) | release_check_notes.gather_notes_facts(
        str(tmp_path), manifest, fake_run
    )
    assert check_notes(facts) == []


def test_check_notes_has_no_drift_for_a_coxswain_bullet_citing_a_pr_in_the_umbrella_history(tmp_path):
    (tmp_path / "coxswain").mkdir()
    notes_dir = tmp_path / "coxswain" / "docs" / "releases"
    notes_dir.mkdir(parents=True)
    (notes_dir / "0.1.0.md").write_text("- coxswain: fixed releasable.yml (#101)\n")
    manifest = {"coxswain": {"version": "0.1.0"}, "components": {}}

    def fake_run(cmd, cwd, capture_output, text):
        stdout = "abc1234 fixed releasable.yml (#101)" if cmd[1] == "log" else ""
        return type("Result", (), {"stdout": stdout})()

    facts = release_check.facts_plan(str(tmp_path), manifest) | release_check_notes.gather_notes_facts(
        str(tmp_path), manifest, fake_run
    )
    assert check_notes(facts) == []


def test_check_notes_drifts_for_a_coxswain_bullet_whose_citation_is_absent_from_the_umbrella_history(tmp_path):
    (tmp_path / "coxswain").mkdir()
    notes_dir = tmp_path / "coxswain" / "docs" / "releases"
    notes_dir.mkdir(parents=True)
    (notes_dir / "0.1.0.md").write_text("- coxswain: fixed releasable.yml (#101)\n")
    manifest = {"coxswain": {"version": "0.1.0"}, "components": {}}

    def fake_run(cmd, cwd, capture_output, text):
        stdout = "abc1234 other change (#7)" if cmd[1] == "log" else ""
        return type("Result", (), {"stdout": stdout})()

    facts = release_check.facts_plan(str(tmp_path), manifest) | release_check_notes.gather_notes_facts(
        str(tmp_path), manifest, fake_run
    )
    drifts = check_notes(facts)
    assert len(drifts) == 1
    assert drifts[0].check == "notes_citation"


def test_parse_bullet_finds_a_prefixed_component_and_a_pr_citation():
    assert parse_bullet("- cox: fixed the thing (#42)", {"cox", "route"}) == ("cox", {"42"})


def test_parse_bullet_finds_a_component_named_anywhere_in_the_text():
    assert parse_bullet("Fixed retries in cox (#42)", {"cox", "route"}) == ("cox", {"42"})


def test_parse_bullet_finds_a_sha_citation():
    assert parse_bullet("- route: bugfix abc1234", {"cox", "route"}) == ("route", {"abc1234"})


def test_parse_bullet_names_no_component_when_none_is_known():
    assert parse_bullet("- something happened (#1)", {"cox"}) == (None, {"1"})


def test_parse_bullet_does_not_treat_a_coxswain_uri_scheme_in_a_code_span_as_a_mention():
    assert parse_bullet("- courier posts a `coxswain://task/x` reference", {"coxswain"}) == (None, set())


def test_parse_bullet_still_finds_a_bare_coxswain_mention_in_prose():
    assert parse_bullet("- the coxswain manifest gains a row", {"coxswain"}) == ("coxswain", set())


def test_parse_bullet_still_finds_a_backtick_quoted_component_name_with_a_citation():
    assert parse_bullet("- fixed `coxswain-tools` publish workflow (#187)", {"coxswain-tools"}) == ("coxswain-tools", {"187"})


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


def test_check_notes_has_no_drift_for_a_coxswain_uri_scheme_in_a_code_span():
    facts = {"component_dirs": {"tools": "/repo/tools", "coxswain": "/repo/coxswain"},
              "landed": {"tools": {"42"}, "coxswain": {"7"}}, "release_notes": "notes.md",
              "notes_bullets": [(3, "- tools: relays to the chair's courier inbox as a `coxswain://task/<id>` reference (#42)")]}
    assert check_notes(facts) == []


def test_check_notes_still_drifts_on_a_bare_coxswain_mention_with_no_citation():
    facts = {"component_dirs": {"coxswain": "/repo/coxswain"}, "landed": {"coxswain": {"42"}}, "release_notes": "notes.md",
              "notes_bullets": [(3, "- the coxswain manifest gains a row")]}
    assert check_notes(facts) == [
        Drift("notes_citation", "notes.md", 3, "/repo/coxswain", None, "cite the PR or commit landed in coxswain")
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


def test_an_unmeasured_pr_citation_is_not_a_drift():
    facts = {"component_dirs": {"tools": "/r/tools"}, "landed": {"tools": set()}, "pr_numbers_measured": {"tools": False},
             "release_notes": "notes.md", "notes_bullets": [(3, "- tools: the gate (#59)")]}
    assert check_notes(facts) == []


def test_bullets_from_notes_joins_a_wrapped_bullets_continuation_lines():
    text = (
        "## graphs\n\n"
        "- The sweep graph exists: module, apply and verify, registered in the CLI\n"
        "  and documented (graphs #83, #84).\n"
        "- A second bullet (graphs #85).\n\n"
        "  A separate indented paragraph after a blank line is not part of any bullet.\n"
    )
    bullets = bullets_from_notes(text)
    assert bullets == [
        (3, "- The sweep graph exists: module, apply and verify, registered in the CLI and documented (graphs #83, #84)."),
        (5, "- A second bullet (graphs #85)."),
    ]


def test_previous_version_is_the_greatest_release_below_the_one_checked():
    assert previous_version("0.11.0", ["0.9.0", "0.10.0", "0.11.0", "0.12.0"]) == "0.10.0"
    assert previous_version("0.1.0", ["0.1.0"]) is None


def test_landed_set_is_the_previous_tag_to_the_versions_own_tag_when_cut_else_to_head(tmp_path):
    (tmp_path / "coxswain").mkdir()
    notes_dir = tmp_path / "coxswain" / "docs" / "releases"
    notes_dir.mkdir(parents=True)
    (notes_dir / "0.10.0.md").write_text("- coxswain: older (#1)\n")
    (notes_dir / "0.11.0.md").write_text("- coxswain: inside the cut (#186)\n- coxswain: after the cut (#190)\n")
    manifest = {"coxswain": {"version": "0.11.0"}, "components": {}}
    logs = {"v0.10.0..v0.11.0": "abc1234 shipped (#186)", "v0.10.0..HEAD": "def5678 later (#190)\nabc1234 shipped (#186)",
            "HEAD": "def5678 later (#190)\nabc1234 shipped (#186)\n0123456 older (#1)"}

    def drift_lines(tags: set[str]) -> list[int]:
        def fake_run(cmd, cwd, capture_output, text):
            stdout = ("9f8e7d6" if cmd[-1] in tags else "") if cmd[1] == "rev-parse" else logs.get(cmd[-1], "")
            return type("Result", (), {"stdout": stdout})()

        facts = release_check.facts_plan(str(tmp_path), manifest) | release_check_notes.gather_notes_facts(
            str(tmp_path), manifest, fake_run
        )
        return [d.a_line for d in check_notes(facts)]

    assert drift_lines({"v0.10.0", "v0.11.0"}) == [2]  # cut: #190 merged after the tag does not count
    assert drift_lines({"v0.10.0"}) == []  # not yet cut: the range runs to HEAD
    assert drift_lines(set()) == []  # no previous tag in this checkout: the whole history
