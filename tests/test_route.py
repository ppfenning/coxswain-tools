import pytest

from agent_tools import route

VALID_PROFILE = """\
team: acme
cartridges_dir: /opt/cartridges
skills_roots: [/opt/skills-a, /opt/skills-b]
provider_profile: /opt/providers/acme.yaml
harness_dir: /opt/agent-graphs
workspace_dir: /home/acme/workspace
assume: y
"""


def test_parse_profile_reads_scalars_and_lists():
    profile = route.parse_profile(VALID_PROFILE)
    assert profile["team"] == "acme"
    assert profile["cartridges_dir"] == "/opt/cartridges"
    assert profile["skills_roots"] == ["/opt/skills-a", "/opt/skills-b"]
    assert profile["provider_profile"] == "/opt/providers/acme.yaml"
    assert profile["harness_dir"] == "/opt/agent-graphs"
    assert profile["workspace_dir"] == "/home/acme/workspace"
    assert profile["assume"] == "y"


def test_parse_profile_defaults_assume_to_a_when_absent():
    text = "team: acme\ncartridges_dir: /opt/cartridges\n"
    profile = route.parse_profile(text)
    assert profile["assume"] == "a"


def test_parse_profile_defaults_assume_to_a_when_value_is_empty():
    text = "team: acme\nassume:\n"
    profile = route.parse_profile(text)
    assert profile["assume"] == "a"


def test_parse_profile_strips_the_spec_sample_inline_comment():
    # spec §1's own sample line, verbatim.
    text = "assume: a          # gate answer for detached runs; drafts only ever land\n"
    profile = route.parse_profile(text)
    assert profile["assume"] == "a"


def test_parse_profile_unknown_key_names_the_line():
    text = "team: acme\nbogus_key: nope\n"
    with pytest.raises(route.ProfileError) as exc_info:
        route.parse_profile(text)
    message = str(exc_info.value)
    assert message.startswith("line 2:")
    assert "bogus_key: nope" in message


def test_parse_profile_nested_key_names_the_line():
    text = "team: acme\n  nested: bad\n"
    with pytest.raises(route.ProfileError) as exc_info:
        route.parse_profile(text)
    message = str(exc_info.value)
    assert message.startswith("line 2:")
    assert "nested: bad" in message


def test_slugify_handles_punctuation_unicode_and_whitespace():
    assert route.slugify("  Fix the Bug!! ") == "fix-the-bug"
    assert route.slugify("Café Résumé — draft") == "caf-r-sum-draft"
    assert route.slugify("multiple   spaces\tand\ttabs") == "multiple-spaces-and-tabs"


def test_slugify_truncates_to_48_characters():
    title = "a" * 60
    slug = route.slugify(title)
    assert len(slug) <= 48
    assert slug == "a" * 48


def test_slugify_returns_empty_string_when_title_has_no_alphanumerics():
    # Pinned, not asserted-away: callers using this as a path segment must
    # guard against the empty result themselves (see route.py docstring).
    assert route.slugify("!!!") == ""
    assert route.slugify("   ") == ""


def test_next_run_id_returns_first_free_id_for_prefix():
    existing = ["myinit-1", "myinit-2", "otherinit-1", "myinit-4"]
    assert route.next_run_id(existing, "myinit") == "myinit-3"


def test_next_run_id_starts_at_one_when_none_exist():
    assert route.next_run_id([], "freshinit") == "freshinit-1"


def test_next_run_id_never_collides_with_existing_names():
    existing = [f"init-{n}" for n in range(1, 6)]
    new_id = route.next_run_id(existing, "init")
    assert new_id not in existing
    assert new_id == "init-6"


def test_next_run_id_matches_real_runs_dir_filenames_not_bare_ids():
    # spec §4/§5: runs_dir holds <run-id>.log and <run-id>.pid, never a
    # bare id — a fixture of bare ids would hide a collision here.
    existing = ["myinit-1.log", "myinit-1.pid", "myinit-2.log", "myinit-2.pid"]
    new_id = route.next_run_id(existing, "myinit")
    assert new_id == "myinit-3"
    assert new_id + ".log" not in existing
    assert new_id + ".pid" not in existing
