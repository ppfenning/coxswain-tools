import json

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


def test_harness_argv_appends_fix_attempts_for_epic_when_given():
    profile = route.parse_profile(VALID_PROFILE)
    argv = route.harness_argv(
        profile, "epic", "myinit-1", initiative="/work/myinit", repo="/repos/widget", fix_attempts=3,
    )
    assert argv[argv.index("--repo") + 2 :] == ["--fix-attempts", "3", "--workdir", "/home/acme/workspace"]


def test_harness_argv_omits_fix_attempts_for_epic_when_absent():
    profile = route.parse_profile(VALID_PROFILE)
    argv = route.harness_argv(
        profile, "epic", "myinit-1", initiative="/work/myinit", repo="/repos/widget",
    )
    assert "--fix-attempts" not in argv


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


def test_child_env_sets_trace_dir_and_still_prepends_path():
    environ = {"PATH": "/usr/bin"}
    env = route.child_env(environ, harness_dir="/opt/agent-graphs", trace_dir="/runs/x-1-trace")
    assert env["AGENT_GRAPHS_TRACE_DIR"] == "/runs/x-1-trace"
    assert env["PATH"] == "/opt/agent-graphs/.venv/bin:/usr/bin"


def test_child_env_without_trace_dir_leaves_the_key_untouched():
    absent = route.child_env({"PATH": "/usr/bin"})
    assert "AGENT_GRAPHS_TRACE_DIR" not in absent
    present = route.child_env({"PATH": "/usr/bin", "AGENT_GRAPHS_TRACE_DIR": "/old"})
    assert present["AGENT_GRAPHS_TRACE_DIR"] == "/old"


def test_child_env_replaces_a_callers_pre_set_trace_dir():
    environ = {"PATH": "/usr/bin", "AGENT_GRAPHS_TRACE_DIR": "/old"}
    env = route.child_env(environ, trace_dir="/runs/x-1-trace")
    assert env["AGENT_GRAPHS_TRACE_DIR"] == "/runs/x-1-trace"


FIXTURE_PROFILE = {
    "team": "acme",
    "cartridges_dir": "/opt/cartridges",
    "skills_roots": ["/opt/skills-a", "/opt/skills-b"],
    "provider_profile": "/opt/providers/acme.yaml",
    "harness_dir": "/opt/agent-graphs",
    "workspace_dir": "/home/acme/workspace",
    "assume": "a",
}

FIXTURE_INTAKE = [
    {"id": "ship-it", "title": "ship it"},
    {"id": "fix-ci", "title": "fix ci"},
]

FIXTURE_RUNS = [
    {"id": "widget-1", "pid": 4242, "alive": True, "started": "14:02"},
]

FIXTURE_INITIATIVES = [
    {"id": "widget", "phase": "build", "ready": 3},
]


def test_render_context_with_profile_matches_spec_layout():
    text = route.render_context(
        FIXTURE_PROFILE, FIXTURE_INTAKE, FIXTURE_RUNS, FIXTURE_INITIATIVES
    )
    assert text == (
        "routing: team acme; work requests go through the route-work skill, "
        "questions stay inline\n"
        'intake: 2 queued — "ship it", "fix ci"\n'
        "runs: 1 in flight — widget-1 (pid 4242, since 14:02)\n"
        "ready: widget (3 tasks ready in phase build)"
    )


def test_render_context_with_profile_and_no_activity():
    text = route.render_context(FIXTURE_PROFILE, [], [], [])
    assert text == (
        "routing: team acme; work requests go through the route-work skill, "
        "questions stay inline\n"
        "intake: 0 queued\n"
        "runs: 0 in flight\n"
        "ready: none"
    )


def test_render_context_without_profile_is_the_one_liner():
    text = route.render_context(None, [], [], [])
    assert text == (
        "routing: no profile at ~/.config/agent-tools/profile.yaml; the "
        "harness is not configured on this machine"
    )


def test_context_document_keys_on_exactly_the_profile_fields():
    doc = route.context_document(
        FIXTURE_PROFILE, FIXTURE_INTAKE, FIXTURE_RUNS, FIXTURE_INITIATIVES
    )
    assert doc["team"] == "acme"
    assert doc["cartridges_dir"] == "/opt/cartridges"
    assert doc["skills_roots"] == ["/opt/skills-a", "/opt/skills-b"]
    assert doc["provider_profile"] == "/opt/providers/acme.yaml"
    assert doc["harness_dir"] == "/opt/agent-graphs"
    assert doc["workspace_dir"] == "/home/acme/workspace"
    assert doc["assume"] == "a"
    assert doc["intake"] == FIXTURE_INTAKE
    assert doc["runs"] == FIXTURE_RUNS
    assert doc["initiatives"] == FIXTURE_INITIATIVES
    assert json.loads(json.dumps(doc)) == doc


FIXTURE_RUNS_MIXED = [
    {"id": "widget-1", "pid": 4242, "alive": True, "started": "14:02"},
    {"id": "widget-2", "pid": 4243, "alive": False, "started": "13:02"},
    {"id": "widget-3", "pid": 4244, "alive": False, "started": "12:02"},
]


def test_render_context_counts_only_alive_runs_in_flight():
    text = route.render_context(
        FIXTURE_PROFILE, FIXTURE_INTAKE, FIXTURE_RUNS_MIXED, FIXTURE_INITIATIVES
    )
    lines = text.splitlines()
    assert "runs: 1 in flight — widget-1 (pid 4242, since 14:02)" in lines
    assert "widget-2" not in text
    assert "widget-3" not in text


def test_render_context_all_exited_reports_zero_in_flight():
    runs = [
        {"id": "widget-2", "pid": 4243, "alive": False, "started": "13:02"},
        {"id": "widget-3", "pid": 4244, "alive": False, "started": "12:02"},
    ]
    text = route.render_context(FIXTURE_PROFILE, FIXTURE_INTAKE, runs, FIXTURE_INITIATIVES)
    lines = text.splitlines()
    assert "runs: 0 in flight" in lines
    assert "widget-2" not in text
    assert "widget-3" not in text


def test_context_document_adds_a_live_count_of_alive_runs():
    doc = route.context_document(
        FIXTURE_PROFILE, FIXTURE_INTAKE, FIXTURE_RUNS_MIXED, FIXTURE_INITIATIVES
    )
    assert doc["live"] == 1
    assert doc["runs"] == FIXTURE_RUNS_MIXED


def test_context_document_without_profile_has_the_same_keys_as_none():
    doc = route.context_document(None, [], [], [])
    for field in (
        "team",
        "cartridges_dir",
        "skills_roots",
        "provider_profile",
        "harness_dir",
        "workspace_dir",
        "assume",
    ):
        assert doc[field] is None
    assert doc["intake"] == []
    assert doc["runs"] == []
    assert doc["initiatives"] == []
    assert json.loads(json.dumps(doc)) == doc


def test_status_rows_covers_live_dead_and_no_pidfile_runs_in_order():
    entries = [
        {
            "id": "widget-1",
            "pid": 4242,
            "alive": True,
            "started": "14:02",
            "quarantined": [],
            "reused": [],
            "summary": None,
            "usage": None,
        },
        {
            "id": "widget-2",
            "pid": 4300,
            "alive": False,
            "started": "13:10",
            "quarantined": ["quarantined task foo"],
            "reused": [],
            "summary": "epic done",
            "usage": "usage $0.42",
        },
        {"id": "widget-3", "pid": None, "alive": None, "started": None},
    ]
    rows = route.status_rows(entries)
    assert [r["id"] for r in rows] == ["widget-1", "widget-2", "widget-3"]
    assert rows[0] == {
        "id": "widget-1",
        "pid": 4242,
        "state": "alive",
        "started": "14:02",
        "quarantined": [],
        "reused": [],
        "summary": None,
        "usage": None,
    }
    assert rows[1] == {
        "id": "widget-2",
        "pid": 4300,
        "state": "exited",
        "started": "13:10",
        "quarantined": ["quarantined task foo"],
        "reused": [],
        "summary": "epic done",
        "usage": "usage $0.42",
    }
    assert rows[2] == {
        "id": "widget-3",
        "pid": None,
        "state": "no pidfile",
        "started": None,
        "quarantined": [],
        "reused": [],
        "summary": None,
        "usage": None,
    }


def test_parse_frontmatter_returns_empty_fields_when_there_is_no_leading_fence():
    text = "Just a plain file.\nNo frontmatter here.\n"
    assert route.parse_frontmatter(text) == ({}, text)


def test_parse_frontmatter_returns_empty_fields_when_the_fence_never_closes():
    text = "---\ntitle: Fix the Bug\nNo closing fence follows.\n"
    assert route.parse_frontmatter(text) == ({}, text)


@pytest.mark.parametrize(
    "title",
    [
        "Fix the Bug",
        "route.py: initiative_files #wip",
        "  Fix the Bug  ",
        "[draft] ship it",
        '"draft" ship it',
    ],
)
def test_parse_frontmatter_round_trips_every_title_shape_initiative_files_can_emit(title):
    files = route.initiative_files(title, "Ship it.", "repo-url")
    task_text = next(v for k, v in files.items() if k.endswith(".md") and "/build/" in k)
    fields, _ = route.parse_frontmatter(task_text)
    assert fields["title"] == title
    assert fields["needs"] == []
    assert fields["surfaces"] == []


@pytest.mark.parametrize(
    "title",
    [
        "Fix the Bug",
        "route.py: initiative_files #wip",
        "  Fix the Bug  ",
        "[draft] ship it",
        '"draft" ship it',
    ],
)
def test_parse_frontmatter_round_trips_every_title_shape_intake_file_can_emit(title):
    files = route.intake_file(title, "Ship it.", "repo-url", "2026-09-04")
    text = next(iter(files.values()))
    fields, _ = route.parse_frontmatter(text)
    assert fields["title"] == title


def test_parse_frontmatter_splits_a_non_empty_list_on_commas():
    # The contract's list form is "flat list of comma-separated bare
    # items", not just the `[]` case every needs/surfaces assertion above
    # exercises. Returning `[inner]` in place of the comma split keeps
    # every other list assertion in this file green, so this is the one
    # test that pins the split and the per-item strip.
    text = "---\nneeds: [a, b]\n---\n\nx\n"
    fields, _ = route.parse_frontmatter(text)
    assert fields["needs"] == ["a", "b"]


def test_parse_frontmatter_round_trips_an_empty_string_field():
    # `title`'s own empty string is unreachable through these builders —
    # `_slug_or_raise` refuses it before any frontmatter is written — but
    # `repo` carries no such guard, so it is what exercises _yaml_scalar's
    # `value == ""` branch end to end here.
    files = route.initiative_files("Fix the Bug", "Ship it.", "")
    initiative_text = files["work/fix-the-bug/initiative.md"]
    fields, _ = route.parse_frontmatter(initiative_text)
    assert fields["repo"] == ""


def test_parse_frontmatter_recovers_the_body_after_the_closing_fence():
    # Exercises the blank-line strip and the trailing-newline strip on
    # their own: swapping either for a bare `after_closing` return still
    # passes every test above (they only check `fields`), so this is the
    # one assertion that would catch that regression.
    files = route.initiative_files("Fix the Bug", "Ship it.", "repo-url")
    task_text = files["work/fix-the-bug/build/fix-the-bug.md"]
    _, body = route.parse_frontmatter(task_text)
    assert body == "Ship it."


def test_parse_frontmatter_never_raises_on_a_header_line_with_no_colon_space():
    # A stray line inside the fence with no `": "` separator — a bad hand
    # edit, a YAML block-list item, a bare comment — is not a line this
    # module ever wrote. The contract says parse_frontmatter never raises
    # on any input, not just well-formed frontmatter, and the next phase's
    # list builders will read whatever is actually on disk.
    text = "---\nnotes\nid: x\n---\n\nbody\n"
    fields, body = route.parse_frontmatter(text)
    assert fields == {"id": "x"}
    assert body == "body"


def test_parse_frontmatter_misparses_a_bare_value_that_happens_to_start_with_a_quote():
    # Parser-side property, not a writer guard: this never calls
    # initiative_files or _yaml_scalar. It hand-builds the frontmatter line
    # the writer would emit for a `"`-leading title *if* it did not quote
    # it, to show why the writer must — parse_frontmatter has no way to
    # tell that unquoted value apart from a real quoted scalar, so it
    # slices the wrong substring back out. The actual guard against a
    # writer regression is the '"draft" ship it' case in the two
    # parametrised round-trip tests above: revert the _yaml_scalar
    # addition and the writer emits this same bare line, so that case's
    # `fields["title"] == title` assertion fails.
    bare_line = 'title: "draft" ship it\n'
    text = f"---\nid: x\n{bare_line}---\n\nShip it.\n"
    fields, _ = route.parse_frontmatter(text)
    assert fields["title"] != '"draft" ship it'


def test_intake_entries_sorts_by_filename_and_skips_consumed_prefix():
    # Filenames supplied out of order and one already-consumed file: the
    # sort and the skip are both live rules, not accidents of dict order.
    files = {
        "2026-09-03-later.md": "---\nid: later\ntitle: Later\n---\n\nBody.\n",
        "2026-09-01-earlier.md": "---\nid: earlier\ntitle: Earlier\n---\n\nBody.\n",
        "consumed/2026-08-01-old.md": "---\nid: old\ntitle: Old\n---\n\nBody.\n",
    }
    entries = route.intake_entries(files)
    assert entries == [
        {"id": "earlier", "title": "Earlier"},
        {"id": "later", "title": "Later"},
    ]


def test_intake_entries_falls_back_to_stem_and_first_body_line_when_frontmatter_is_absent():
    files = {"2026-09-02-no-frontmatter.md": "\n\nFirst real line.\nSecond line.\n"}
    entries = route.intake_entries(files)
    assert entries == [
        {"id": "2026-09-02-no-frontmatter", "title": "First real line."}
    ]


def test_run_entries_sorts_by_id_and_treats_a_non_integer_pidfile_as_dead():
    # Ids supplied out of order and one pidfile whose text is not an
    # integer — a partial write — must come back pid None, alive False even
    # though the caller's own `alive` map says True.
    pids = {"r2": "222", "r1": "not-a-pid"}
    alive = {"r1": True, "r2": True}
    started = {"r1": "2026-09-04T00:00:00", "r2": "2026-09-04T00:01:00"}
    entries = route.run_entries(pids, alive, started)
    assert entries == [
        {"id": "r1", "pid": None, "alive": False, "started": "2026-09-04T00:00:00"},
        {"id": "r2", "pid": 222, "alive": True, "started": "2026-09-04T00:01:00"},
    ]


def test_initiative_summaries_picks_the_sorted_first_phase_with_a_ready_task():
    # Two phases both hold a ready, unblocked task; the alphabetically
    # first phase name must win, not first-in-list or last-in-list.
    items = [
        {"id": "a1", "initiative": "alpha", "phase": "zzz-later", "state": "ready", "needs": []},
        {"id": "a2", "initiative": "alpha", "phase": "aaa-first", "state": "ready", "needs": []},
    ]
    summaries = route.initiative_summaries(items)
    assert summaries == [{"id": "alpha", "phase": "aaa-first", "ready": 1}]


def test_initiative_summaries_excludes_a_task_whose_needs_are_not_all_done():
    # The one ready task in this initiative needs a task that is not done,
    # so it must not be counted, and the initiative must not appear at all.
    items = [
        {"id": "b1", "initiative": "beta", "phase": "build", "state": "ready", "needs": ["b0"]},
        {"id": "b0", "initiative": "beta", "phase": "build", "state": "in-progress", "needs": []},
    ]
    assert route.initiative_summaries(items) == []


def test_initiative_summaries_ready_is_a_count_not_a_flag():
    # Two ready, unblocked tasks share the winning phase and a third ready
    # task sits in a losing phase; a literal 1 in place of a real count
    # would pass every other case in this file but fails here.
    items = [
        {"id": "g1", "initiative": "gamma", "phase": "build", "state": "ready", "needs": []},
        {"id": "g2", "initiative": "gamma", "phase": "build", "state": "ready", "needs": []},
        {"id": "g3", "initiative": "gamma", "phase": "later", "state": "ready", "needs": []},
    ]
    summaries = route.initiative_summaries(items)
    assert summaries == [{"id": "gamma", "phase": "build", "ready": 2}]


def test_initiative_summaries_sorts_rows_by_initiative_id():
    # Six initiatives, each with one ready, unblocked task, supplied in
    # reverse order. Grouping them with a bare set (no `sorted`) would come
    # back in whatever order Python's per-process hash randomisation gives
    # a six-element string set, which matches this exact ascending order by
    # chance on 1 run in 720 — this is a real pin, not a coin flip.
    items = [
        {"id": f"{name[0]}1", "initiative": name, "phase": "build", "state": "ready", "needs": []}
        for name in ["zulu", "yankee", "xray", "whiskey", "victor", "alpha"]
    ]
    summaries = route.initiative_summaries(items)
    assert [row["id"] for row in summaries] == [
        "alpha", "victor", "whiskey", "xray", "yankee", "zulu",
    ]


def test_intake_entries_falls_back_to_id_when_frontmatter_has_no_title_and_body_is_empty():
    # Frontmatter with an id but no title, and no body line at all: title
    # must fall back to id, not raise on an empty body_lines list.
    files = {"any-name.md": "---\nid: notitle\n---\n\n"}
    entries = route.intake_entries(files)
    assert entries == [{"id": "notitle", "title": "notitle"}]


def test_parse_pid_reads_a_plain_digit_string():
    assert route.parse_pid("4821") == 4821


def test_parse_pid_returns_none_for_an_empty_string():
    assert route.parse_pid("") is None
    assert route.parse_pid("   ") is None


def test_parse_pid_returns_none_for_garbage():
    assert route.parse_pid("garbage") is None


def test_render_status_no_pidfile_row_omits_pid_and_started():
    rows = [{"id": "r1", "pid": None, "state": "no pidfile", "started": None,
             "quarantined": [], "reused": [], "summary": None, "usage": None}]
    assert route.render_status(rows) == "r1: no pidfile"


def test_render_status_quiet_row_with_a_pid_stays_one_line():
    rows = [{"id": "r2", "pid": 123, "state": "alive", "started": "2026-09-04T00:00:00+00:00",
             "quarantined": [], "reused": [], "summary": None, "usage": None}]
    assert route.render_status(rows) == "r2: alive (pid 123, started 2026-09-04T00:00:00+00:00)"


def test_render_status_appends_quarantined_reused_summary_and_usage():
    rows = [{"id": "r3", "pid": None, "state": "no pidfile", "started": None,
             "quarantined": ["quarantined task: a — reason"], "reused": ["reused b from run-1"],
             "summary": "epic run-2: done", "usage": "usage: 1 call"}]
    assert route.render_status(rows) == (
        "r3: no pidfile quarantined task: a — reason reused b from run-1 epic run-2: done usage: 1 call"
    )


def test_render_status_joins_multiple_rows_with_one_newline_each():
    rows = [
        {"id": "a", "pid": None, "state": "no pidfile", "started": None,
         "quarantined": [], "reused": [], "summary": None, "usage": None},
        {"id": "b", "pid": 7, "state": "exited", "started": "t",
         "quarantined": [], "reused": [], "summary": None, "usage": None},
    ]
    assert route.render_status(rows) == "a: no pidfile\nb: exited (pid 7, started t)"


def test_status_entries_keeps_a_pid_bearing_run_untouched_when_it_has_no_log():
    runs = [{"id": "run1", "pid": 123, "alive": True, "started": "t1"}]
    assert route.status_entries(runs, {}) == [{"id": "run1", "pid": 123, "alive": True, "started": "t1"}]


def test_status_entries_merges_a_pid_bearing_run_with_its_log_summary():
    runs = [{"id": "run1", "pid": 123, "alive": True, "started": "t1"}]
    summaries = {"run1": {"quarantined": ["quarantined task: a"], "reused": [], "summary": "epic run-1", "usage": None}}
    assert route.status_entries(runs, summaries) == [
        {"id": "run1", "pid": 123, "alive": True, "started": "t1",
         "quarantined": ["quarantined task: a"], "reused": [], "summary": "epic run-1", "usage": None}
    ]


def test_status_entries_gives_a_log_only_id_the_pid_less_shape():
    summaries = {"run2": {"quarantined": [], "reused": [], "summary": "epic run-2", "usage": None}}
    assert route.status_entries([], summaries) == [
        {"id": "run2", "pid": None, "alive": False, "started": None,
         "quarantined": [], "reused": [], "summary": "epic run-2", "usage": None}
    ]


def test_status_entries_sorts_the_union_of_run_and_log_ids():
    runs = [{"id": "b", "pid": 1, "alive": True, "started": "t"}]
    summaries = {"a": {}, "c": {}}
    ids = [entry["id"] for entry in route.status_entries(runs, summaries)]
    assert ids == ["a", "b", "c"]


def test_work_item_passes_full_frontmatter_through():
    fields = {"id": "t1", "phase": "1-build", "state": "ready", "needs": ["t0"]}
    item = route.work_item(fields, initiative="demo", phase_dir="9-ignored", stem="ignored")
    assert item == {"id": "t1", "initiative": "demo", "phase": "1-build", "state": "ready", "needs": ["t0"]}


def test_work_item_defaults_every_field_from_the_path_when_frontmatter_is_empty():
    item = route.work_item({}, initiative="demo", phase_dir="1-build", stem="task")
    assert item == {"id": "task", "initiative": "demo", "phase": "1-build", "state": "todo", "needs": []}


def test_work_item_initiative_argument_wins_over_a_frontmatter_key():
    fields = {"initiative": "wrong"}
    item = route.work_item(fields, initiative="demo", phase_dir="1-build", stem="task")
    assert item["initiative"] == "demo"
