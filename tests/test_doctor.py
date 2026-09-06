import re

from agent_tools import doctor

_CHECK_ORDER = (
    "profile",
    "profile paths",
    "harness venv",
    "core importable",
    "cartridge",
    "project overlay",
    "skills",
    "provider",
    "workspace",
)


def _good_facts():
    return {
        "profile_path": "/profiles/a.yaml",
        "profile_text": "team: pat\ncartridges_dir: /c\nskills_roots: [/s1]\nprovider_profile: /p.yaml\nharness_dir: /h\nworkspace_dir: /w\n",
        "paths_exist": {"/c": True, "/s1": True, "/p.yaml": True, "/h": True, "/w": True},
        "harness_python_exists": True,
        "core_import": None,
        "cartridge_load": None,
        "overlay_errors": None,
        "skill_roots_indexed": {"/s1": 3},
        "provider_command": "claude",
        "provider_on_path": True,
        "provider_version": "claude 1.2.3",
        "workspace_dirs": {"/w/work": True, "/w/runs": True, "/w/intake": True},
    }


def _rows_by_check(rows):
    return {r["check"]: r for r in rows}


def _rows_for(rows, check):
    return [r for r in rows if r["check"] == check]


def test_all_good_facts_yield_every_row_ok_and_exit_zero():
    rows = doctor.checks(_good_facts())
    assert all(r["ok"] for r in rows)
    assert doctor.exit_code(rows) == 0


def test_checks_returns_rows_in_the_fixed_order():
    rows = doctor.checks(_good_facts())
    assert [r["check"] for r in rows] == list(_CHECK_ORDER)


def test_a_none_overlay_errors_fact_gives_an_ok_row_naming_no_project_overlay():
    rows = _rows_by_check(doctor.checks(_good_facts()))
    assert rows["project overlay"] == {"check": "project overlay", "ok": True, "detail": "no project overlay"}


def test_empty_overlay_errors_gives_an_ok_row():
    facts = _good_facts()
    facts["overlay_errors"] = []
    rows = _rows_by_check(doctor.checks(facts))
    assert rows["project overlay"] == {"check": "project overlay", "ok": True, "detail": "ok"}


def test_a_refused_overlay_key_fails_the_row_and_names_it():
    facts = _good_facts()
    facts["overlay_errors"] = ["skills is refused in a project overlay"]
    rows = _rows_by_check(doctor.checks(facts))
    assert rows["project overlay"] == {
        "check": "project overlay", "ok": False, "detail": "skills is refused in a project overlay",
    }


def test_absent_profile_fails_profile_and_skips_dependent_rows():
    facts = _good_facts()
    facts["profile_text"] = None
    rows = _rows_by_check(doctor.checks(facts))
    assert rows["profile"]["ok"] is False
    assert "/profiles/a.yaml" in rows["profile"]["detail"]
    for check in ("profile paths", "harness venv", "core importable", "cartridge", "project overlay", "skills", "provider", "workspace"):
        assert rows[check]["ok"] is False
        assert rows[check]["detail"] == "skipped: no profile"


def test_unparseable_profile_carries_the_parsers_message():
    facts = _good_facts()
    facts["profile_text"] = "  team: pat\n"
    rows = _rows_by_check(doctor.checks(facts))
    assert rows["profile"]["ok"] is False
    assert "line 1" in rows["profile"]["detail"]


def test_one_missing_path_is_named_in_a_failing_profile_paths_row():
    facts = _good_facts()
    facts["paths_exist"]["/s1"] = False
    rows = _rows_for(doctor.checks(facts), "profile paths")
    assert len(rows) == 1
    assert rows[0]["ok"] is False
    assert "/s1" in rows[0]["detail"]


def test_two_missing_paths_produce_two_failing_profile_paths_rows_not_one():
    facts = _good_facts()
    facts["paths_exist"]["/s1"] = False
    facts["paths_exist"]["/h"] = False
    rows = _rows_for(doctor.checks(facts), "profile paths")
    assert len(rows) == 2
    assert all(r["ok"] is False for r in rows)
    assert {r["detail"] for r in rows} == {"missing: /h", "missing: /s1"}


def test_all_paths_present_gives_one_ok_row_naming_the_count():
    rows = _rows_by_check(doctor.checks(_good_facts()))
    assert rows["profile paths"]["ok"] is True
    assert "5" in rows["profile paths"]["detail"]


def test_empty_paths_exist_fails_profile_paths_instead_of_passing_vacuously():
    facts = _good_facts()
    facts["paths_exist"] = {}
    rows = _rows_by_check(doctor.checks(facts))
    assert rows["profile paths"]["ok"] is False


def test_a_path_the_profile_configures_but_the_edge_never_gathered_fails_profile_paths():
    facts = _good_facts()
    del facts["paths_exist"]["/s1"]
    rows = _rows_for(doctor.checks(facts), "profile paths")
    assert len(rows) == 1
    assert rows[0]["ok"] is False
    assert "/s1" in rows[0]["detail"]


def test_missing_harness_venv_fails_that_row():
    facts = _good_facts()
    facts["harness_python_exists"] = False
    rows = _rows_by_check(doctor.checks(facts))
    assert rows["harness venv"]["ok"] is False


def test_core_import_error_text_appears_in_detail():
    facts = _good_facts()
    facts["core_import"] = "ModuleNotFoundError: no module named core"
    rows = _rows_by_check(doctor.checks(facts))
    assert rows["core importable"]["ok"] is False
    assert "no module named core" in rows["core importable"]["detail"]


def test_cartridge_load_error_appears_in_detail():
    facts = _good_facts()
    facts["cartridge_load"] = "team 'pat' has no bound skill 'foo'"
    rows = _rows_by_check(doctor.checks(facts))
    assert rows["cartridge"]["ok"] is False
    assert "no bound skill" in rows["cartridge"]["detail"]


def test_a_root_indexing_zero_skills_fails_skills():
    facts = _good_facts()
    facts["skill_roots_indexed"] = {"/s1": 0}
    rows = _rows_by_check(doctor.checks(facts))
    assert rows["skills"]["ok"] is False
    assert "/s1" in rows["skills"]["detail"]


def test_empty_skill_roots_indexed_fails_skills_instead_of_passing_vacuously():
    facts = _good_facts()
    facts["skill_roots_indexed"] = {}
    rows = _rows_by_check(doctor.checks(facts))
    assert rows["skills"]["ok"] is False


def test_a_skill_root_the_profile_configures_but_never_indexed_fails_skills():
    facts = _good_facts()
    facts["profile_text"] = (
        "team: pat\ncartridges_dir: /c\nskills_roots: [/s1, /s2]\n"
        "provider_profile: /p.yaml\nharness_dir: /h\nworkspace_dir: /w\n"
    )
    facts["paths_exist"]["/s2"] = True
    rows = _rows_by_check(doctor.checks(facts))
    assert rows["skills"]["ok"] is False
    assert "/s2" in rows["skills"]["detail"]


def test_provider_not_on_path_fails_with_the_command_named():
    facts = _good_facts()
    facts["provider_on_path"] = False
    rows = _rows_by_check(doctor.checks(facts))
    assert rows["provider"]["ok"] is False
    assert "claude" in rows["provider"]["detail"]


def test_provider_command_unreadable_fails_without_blaming_path():
    facts = _good_facts()
    facts["provider_command"] = None
    rows = _rows_by_check(doctor.checks(facts))
    assert rows["provider"]["ok"] is False
    assert "not on PATH" not in rows["provider"]["detail"]


def test_provider_on_path_none_is_not_conflated_with_not_on_path():
    facts = _good_facts()
    facts["provider_on_path"] = None
    rows = _rows_by_check(doctor.checks(facts))
    assert rows["provider"]["ok"] is False
    assert rows["provider"]["detail"] != "not on PATH: claude"


def test_provider_command_key_absent_but_on_path_true_does_not_claim_unreadable():
    facts = _good_facts()
    del facts["provider_command"]
    rows = _rows_by_check(doctor.checks(facts))
    assert rows["provider"]["ok"] is True
    assert "unreadable" not in rows["provider"]["detail"]


def test_provider_version_string_appears_in_detail_when_ok():
    rows = _rows_by_check(doctor.checks(_good_facts()))
    assert rows["provider"]["ok"] is True
    assert "claude 1.2.3" in rows["provider"]["detail"]


def test_a_missing_workspace_dir_is_named():
    facts = _good_facts()
    facts["workspace_dirs"]["/w/runs"] = False
    rows = _rows_by_check(doctor.checks(facts))
    assert rows["workspace"]["ok"] is False
    assert "/w/runs" in rows["workspace"]["detail"]


def test_empty_workspace_dirs_fails_workspace_instead_of_passing_vacuously():
    facts = _good_facts()
    facts["workspace_dirs"] = {}
    rows = _rows_by_check(doctor.checks(facts))
    assert rows["workspace"]["ok"] is False


def test_render_lists_every_row_in_order_with_its_own_check_label():
    rows = doctor.checks(_good_facts())
    text = doctor.render(rows)
    lines = text.splitlines()
    data_lines = lines[1 : 1 + len(rows)]
    labels = [re.split(r"\s{2,}", line.strip())[0] for line in data_lines]
    assert labels == list(_CHECK_ORDER)
    assert lines[-1] == "doctor: 9 ok, 0 failing"


def test_render_marks_a_failing_row_as_fail_and_counts_it():
    facts = _good_facts()
    facts["harness_python_exists"] = False
    rows = doctor.checks(facts)
    text = doctor.render(rows)
    assert "FAIL" in text
    assert "doctor: 8 ok, 1 failing" in text


def test_empty_facts_dict_yields_all_rows_not_checked_and_exit_one():
    rows = doctor.checks({})
    assert all(r["ok"] is False and r["detail"] == "not checked" for r in rows)
    assert doctor.exit_code(rows) == 1
