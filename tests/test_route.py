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


def test_initiative_files_returns_the_initiative_and_task_paths_and_text():
    files = route.initiative_files(
        "Fix the Bug", "Do the thing.", "git@example.com:acme/widget.git", phase="build"
    )
    assert set(files) == {
        "work/fix-the-bug/initiative.md",
        "work/fix-the-bug/build/fix-the-bug.md",
    }
    assert files["work/fix-the-bug/initiative.md"] == (
        "---\n"
        "id: fix-the-bug\n"
        "title: Fix the Bug\n"
        'repo: "git@example.com:acme/widget.git"\n'
        "---\n"
        "\n"
        "Do the thing.\n"
    )
    assert files["work/fix-the-bug/build/fix-the-bug.md"] == (
        "---\n"
        "id: fix-the-bug\n"
        "phase: build\n"
        "state: ready\n"
        "needs: []\n"
        "surfaces: []\n"
        "title: Fix the Bug\n"
        "---\n"
        "\n"
        "Do the thing.\n"
    )


def test_initiative_files_quotes_a_title_containing_yaml_metacharacters():
    # The colon and the `#` are both in the *title* here, not the body, so
    # this exercises both branches of _yaml_scalar's quoting predicate
    # (a bare `#` reads as a comment to this module's own parse_profile).
    files = route.initiative_files(
        "route.py: initiative_files #wip", "Ship it.", "repo-url"
    )
    slug = route.slugify("route.py: initiative_files #wip")
    initiative_text = files[f"work/{slug}/initiative.md"]
    task_text = files[f"work/{slug}/build/{slug}.md"]
    assert 'title: "route.py: initiative_files #wip"' in initiative_text
    assert 'title: "route.py: initiative_files #wip"' in task_text
    # needs/surfaces stay bare list literals, not quoted strings.
    assert "needs: []" in task_text
    assert "surfaces: []" in task_text


def test_initiative_files_quotes_a_bracketed_title_instead_of_treating_it_as_raw_yaml():
    # A title that merely *looks* like a YAML list must not be classified
    # as one by its shape — only this module's own needs/surfaces literals
    # are raw. Unquoted, a real YAML reader would parse `[draft] ship it`
    # as a flow sequence, not the title string.
    files = route.initiative_files("[draft] ship it", "Ship it.", "repo-url")
    slug = route.slugify("[draft] ship it")
    initiative_text = files[f"work/{slug}/initiative.md"]
    assert 'title: "[draft] ship it"' in initiative_text


def test_initiative_files_refuses_a_title_that_slugifies_to_empty():
    # slugify's own docstring hands this guard to callers using the slug
    # as a path segment; a bare "work/initiative.md" write is the wrong
    # write into the work store this guard exists to prevent.
    with pytest.raises(ValueError):
        route.initiative_files("!!!", "body", "repo-url")


def test_initiative_files_defaults_phase_to_build_and_body_to_title():
    files = route.initiative_files("Fix the Bug", "", "repo-url")
    task_text = files["work/fix-the-bug/build/fix-the-bug.md"]
    assert "phase: build\n" in task_text
    assert task_text.endswith("\nFix the Bug\n")


def test_intake_file_returns_the_dated_path_and_text():
    files = route.intake_file(
        "Fix the Bug", "Do the thing.", "git@example.com:acme/widget.git", "2026-09-03"
    )
    assert set(files) == {"intake/2026-09-03-fix-the-bug.md"}
    assert files["intake/2026-09-03-fix-the-bug.md"] == (
        "---\n"
        "id: fix-the-bug\n"
        "title: Fix the Bug\n"
        'repo: "git@example.com:acme/widget.git"\n'
        "---\n"
        "\n"
        "Do the thing.\n"
    )


def test_intake_file_quotes_a_title_containing_yaml_metacharacters():
    files = route.intake_file(
        "route.py: initiative_files #wip", "Ship it.", "repo-url", "2026-09-03"
    )
    slug = route.slugify("route.py: initiative_files #wip")
    text = files[f"intake/2026-09-03-{slug}.md"]
    assert 'title: "route.py: initiative_files #wip"' in text


def test_intake_file_refuses_a_title_that_slugifies_to_empty():
    with pytest.raises(ValueError):
        route.intake_file("!!!", "body", "repo-url", "2026-09-03")


def test_harness_argv_builds_the_epic_command_line():
    profile = route.parse_profile(VALID_PROFILE)
    argv = route.harness_argv(
        profile, "epic", "myinit-1", initiative="/work/myinit", repo="/repos/widget"
    )
    assert argv == [
        "/opt/agent-graphs/.venv/bin/python",
        "/opt/agent-graphs/shell.py",
        "epic",
        "--team",
        "acme",
        "--cartridges-dir",
        "/opt/cartridges",
        "--skills-root",
        "/opt/skills-a",
        "--skills-root",
        "/opt/skills-b",
        "--provider-profile",
        "/opt/providers/acme.yaml",
        "--runs-dir",
        "/home/acme/workspace/runs",
        "--assume",
        "y",
        "--run-id",
        "myinit-1",
        "--initiative",
        "/work/myinit",
        "--repo",
        "/repos/widget",
        "--workdir",
        "/home/acme/workspace",
    ]


def test_harness_argv_builds_the_decompose_command_line():
    profile = route.parse_profile(VALID_PROFILE)
    argv = route.harness_argv(
        profile,
        "decompose",
        "myidea-1",
        idea="/intake/myidea.md",
        initiative_id="myidea",
    )
    assert argv == [
        "/opt/agent-graphs/.venv/bin/python",
        "/opt/agent-graphs/shell.py",
        "decompose",
        "--team",
        "acme",
        "--cartridges-dir",
        "/opt/cartridges",
        "--skills-root",
        "/opt/skills-a",
        "--skills-root",
        "/opt/skills-b",
        "--provider-profile",
        "/opt/providers/acme.yaml",
        "--runs-dir",
        "/home/acme/workspace/runs",
        "--assume",
        "y",
        "--run-id",
        "myidea-1",
        "--idea",
        "/intake/myidea.md",
        "--initiative-id",
        "myidea",
        "--workdir",
        "/home/acme/workspace",
    ]


def test_child_env_prepends_both_venv_bins_and_leaves_other_keys_untouched():
    environ = {"PATH": "/usr/bin:/bin", "HOME": "/home/acme", "LANG": "C.UTF-8"}
    env = route.child_env(environ, harness_dir="/opt/agent-graphs", repo="/repos/widget")
    assert env["PATH"] == "/repos/widget/.venv/bin:/opt/agent-graphs/.venv/bin:/usr/bin:/bin"
    assert env["HOME"] == "/home/acme"
    assert env["LANG"] == "C.UTF-8"
    assert environ["PATH"] == "/usr/bin:/bin"  # input untouched


def test_child_env_omits_a_prefix_that_is_not_given():
    environ = {"PATH": "/usr/bin"}
    env = route.child_env(environ, harness_dir="/opt/agent-graphs", repo="")
    assert env["PATH"] == "/opt/agent-graphs/.venv/bin:/usr/bin"
