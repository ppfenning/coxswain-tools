import re

from agent_tools import doctor, home

_CHECK_ORDER = (
    "git",
    "forge",
    "profile",
    "profile paths",
    "harness venv",
    "core importable",
    "cartridge",
    "project overlay",
    "skills",
    "provider",
    "plugins",
    "store",
    "workspace",
    "schema",
    "cast",
    "chair unit PATH",
)


def _good_facts():
    return {
        "git_version": "git version 2.45.0",
        "forge": "local",
        "forge_found": True,
        "profile_path": "/profiles/a.yaml",
        "profile_text": "team: example-team\ncartridges_dir: /c\nskills_roots: [/s1]\nprovider_profile: /p.yaml\nharness_dir: /h\nworkspace_dir: /w\n",
        "paths_exist": {"/c": True, "/s1": True, "/p.yaml": True, "/h": True, "/w": True},
        "harness_python_exists": True,
        "core_import": None,
        "cartridge_load": None,
        "overlay_errors": None,
        "skill_roots_indexed": {"/s1": 3},
        "provider_command": "claude",
        "provider_on_path": True,
        "provider_version": "claude 1.2.3",
        "store": {"kind": "sqlite", "reachable": True, "error": None, "runs": 593},
        "workspace_dirs": {"/w/work": True, "/w/runs": True, "/w/intake": True},
        "schema_versions": {"cartridges": "1.0", "graphs": "1.0", "tools": "1.0"},
        "cast_seats": {},
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
    for check in ("profile paths", "harness venv", "core importable", "cartridge", "project overlay", "skills", "provider", "workspace", "cast"):
        assert rows[check]["ok"] is False
        assert rows[check]["detail"] == "skipped: no profile"


def test_unparseable_profile_carries_the_parsers_message():
    facts = _good_facts()
    facts["profile_text"] = "  team: example-team\n"
    rows = _rows_by_check(doctor.checks(facts))
    assert rows["profile"]["ok"] is False
    assert "line 1" in rows["profile"]["detail"]


def test_unknown_profile_key_is_a_warning_row_not_a_failure():
    facts = _good_facts()
    facts["profile_text"] += "colour: blue\n"
    rows = doctor.checks(facts)
    warned = [r for r in rows if r.get("warn")]
    assert warned == [{"check": "profile", "ok": True, "warn": True, "detail": "unknown key colour at line 7"}]
    assert all(r["ok"] for r in rows)
    assert doctor.exit_code(rows) == 0
    rendered = doctor.render(rows)
    assert re.search(r"profile\s+warn\s+unknown key colour at line 7", rendered)
    assert "1 warning" in rendered


def test_a_profile_with_no_unknown_key_has_no_warning_row():
    rows = doctor.checks(_good_facts())
    assert not any(r.get("warn") for r in rows)
    assert "warning" not in doctor.render(rows)


def test_malformed_known_key_still_fails_with_an_unknown_key_present():
    facts = _good_facts()
    facts["profile_text"] += "colour: blue\nspend: 5\n"
    rows = doctor.checks(facts)
    assert _rows_for(rows, "profile")[0]["ok"] is False
    assert not any(r.get("warn") for r in rows)
    assert doctor.exit_code(rows) == 1


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
    facts["cartridge_load"] = "team 'example-team' has no bound skill 'foo'"
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
        "team: example-team\ncartridges_dir: /c\nskills_roots: [/s1, /s2]\n"
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
    assert lines[-1] == "doctor: 16 ok, 0 failing"


def test_render_marks_a_failing_row_as_fail_and_counts_it():
    facts = _good_facts()
    facts["harness_python_exists"] = False
    rows = doctor.checks(facts)
    text = doctor.render(rows)
    assert "FAIL" in text
    assert "doctor: 15 ok, 1 failing" in text


def test_empty_facts_dict_yields_all_rows_not_checked_and_exit_one():
    rows = _rows_by_check(doctor.checks({}))
    assert all(r["ok"] is False and r["detail"] == "not checked"
               for check, r in rows.items() if check not in ("cast", "schema", "plugins", "chair unit PATH"))
    assert rows["cast"] == {"check": "cast", "ok": True, "detail": "not gathered"}
    assert rows["chair unit PATH"] == {"check": "chair unit PATH", "ok": True, "detail": "not gathered"}
    assert rows["schema"] == {"check": "schema", "ok": True, "detail": ""}
    assert doctor.exit_code(rows.values()) == 1


def test_all_agreeing_known_schema_majors_give_an_ok_schema_row():
    rows = _rows_by_check(doctor.checks(_good_facts()))
    assert rows["schema"] == {"check": "schema", "ok": True, "detail": "cartridges 1.0, graphs 1.0, tools 1.0"}


def test_a_differing_schema_major_fails_the_schema_row_naming_only_the_outlier():
    facts = _good_facts()
    facts["schema_versions"] = {"cartridges": "1.0", "graphs": "2.0", "tools": "1.0"}
    rows = _rows_by_check(doctor.checks(facts))
    assert rows["schema"] == {"check": "schema", "ok": False, "detail": "graphs 2.0"}


def test_all_enabled_seats_installed_and_all_disabled_seats_absent_gives_an_ok_cast_row():
    facts = _good_facts()
    facts["cast_seats"] = {
        "reviewer": {"enabled": True, "installed": True},
        "scribe": {"enabled": False, "installed": False},
    }
    rows = _rows_by_check(doctor.checks(facts))
    assert rows["cast"] == {"check": "cast", "ok": True, "detail": "2 seats"}


def test_a_missing_enabled_seat_fails_the_cast_row_naming_it():
    facts = _good_facts()
    facts["cast_seats"] = {"reviewer": {"enabled": True, "installed": False}}
    rows = _rows_by_check(doctor.checks(facts))
    assert rows["cast"] == {"check": "cast", "ok": False, "detail": "missing: reviewer"}


def test_an_installed_disabled_seat_fails_the_cast_row_naming_it():
    facts = _good_facts()
    facts["cast_seats"] = {"reviewer": {"enabled": False, "installed": True}}
    rows = _rows_by_check(doctor.checks(facts))
    assert rows["cast"] == {"check": "cast", "ok": False, "detail": "present: reviewer"}


def test_a_missing_git_fails_the_git_row_and_names_git():
    facts = _good_facts()
    facts["git_version"] = None
    row = _rows_by_check(doctor.checks(facts))["git"]
    assert row["ok"] is False
    assert row["detail"].startswith("missing: install git")


def test_a_git_version_line_passes_the_git_row_and_is_the_detail():
    row = _rows_by_check(doctor.checks(_good_facts()))["git"]
    assert row == {"check": "git", "ok": True, "detail": "git version 2.45.0"}


def test_the_local_forge_passes_with_no_gh_fact():
    row = _rows_by_check(doctor.checks(_good_facts()))["forge"]
    assert row == {"check": "forge", "ok": True, "detail": "local: plain git, no pull-request host"}


def test_the_github_forge_without_gh_auth_fails_naming_gh_auth_login():
    facts = _good_facts() | {"forge": "github", "gh_auth": False}
    row = _rows_by_check(doctor.checks(facts))["forge"]
    assert row["ok"] is False
    assert "gh auth login" in row["detail"]


def test_the_github_forge_with_gh_auth_passes():
    facts = _good_facts() | {"forge": "github", "gh_auth": True}
    assert _rows_by_check(doctor.checks(facts))["forge"] == {"check": "forge", "ok": True, "detail": "github: gh authenticated"}


def test_an_unknown_forge_fails_naming_it():
    facts = _good_facts() | {"forge": "gitlab", "forge_found": False}
    row = _rows_by_check(doctor.checks(facts))["forge"]
    assert row == {"check": "forge", "ok": False, "detail": "no forge named gitlab installed"}


def test_render_adds_the_next_step_line_only_for_a_missing_profile():
    next_line = "next: run `cox setup` to write a profile, or `cox install` to fetch every component"
    missing = _good_facts() | {"profile_text": None}
    assert doctor.render(doctor.checks(missing)).splitlines()[-1] == next_line
    unparseable = _good_facts() | {"profile_text": "- not a mapping\n"}
    assert next_line not in doctor.render(doctor.checks(unparseable))
    assert next_line not in doctor.render(doctor.checks(_good_facts() | {"git_version": None}))


_TOOLS = {"coxswain.sources": ["alpha", "beta"], "coxswain.forges": [], "coxswain.trackers": []}
_HARNESS = {"coxswain.system_one": ["jev"], "coxswain.runners": []}


def test_plugins_row_lists_tools_groups_then_harness_groups_with_none_for_empty():
    facts = _good_facts() | {"plugins_tools": _TOOLS, "plugins_harness": _HARNESS}
    assert _rows_by_check(doctor.checks(facts))["plugins"] == {
        "check": "plugins", "ok": True,
        "detail": "sources: alpha, beta; forges: none; trackers: none; system_one: jev; runners: none",
    }


def test_plugins_row_reads_not_checked_for_harness_groups_when_the_probe_reported_none():
    facts = _good_facts() | {"plugins_tools": _TOOLS}
    assert _rows_by_check(doctor.checks(facts))["plugins"]["detail"] == (
        "sources: alpha, beta; forges: none; trackers: none; system_one: not checked; runners: not checked")


def test_plugins_row_is_ok_and_does_not_cascade_with_nothing_gathered_or_no_profile():
    rows = _rows_by_check(doctor.checks({}))
    assert rows["plugins"]["ok"] is True
    assert "sources: not checked" in rows["plugins"]["detail"]
    missing = _rows_by_check(doctor.checks(_good_facts() | {"profile_text": None}))
    assert missing["plugins"]["ok"] is True
    assert "skipped" not in missing["plugins"]["detail"]


def test_gather_reads_tools_plugin_names_per_group_sorted(monkeypatch, tmp_path):
    import importlib.metadata
    from types import SimpleNamespace

    from agent_tools.cli import _gather_doctor_facts
    names = {"coxswain.sources": ["beta", "alpha"], "coxswain.forges": [], "coxswain.trackers": ["linear"]}
    monkeypatch.setattr(importlib.metadata, "entry_points",
                        lambda group: [SimpleNamespace(name=n) for n in names[group]])
    facts = _gather_doctor_facts(tmp_path / "absent.yaml", tmp_path)
    assert facts["plugins_tools"] == {"coxswain.sources": ["alpha", "beta"], "coxswain.forges": [],
                                      "coxswain.trackers": ["linear"]}


def _store_row_for(store):
    return _rows_by_check(doctor.checks(_good_facts() | {"store": store}))["store"]


def test_store_row_names_the_kind_and_run_count_when_reachable():
    assert _store_row_for({"kind": "sqlite", "reachable": True, "error": None, "runs": 593}) == {
        "check": "store", "ok": True, "detail": "sqlite, 593 runs"}
    assert _store_row_for({"kind": "postgresql", "reachable": True, "error": None, "runs": 12})["detail"] == "postgresql, 12 runs"


def test_store_row_fails_with_the_missing_driver_message_for_postgres():
    from agent_tools.store_dialect import MISSING_PSYCOPG
    row = _store_row_for({"kind": "postgresql", "reachable": False, "error": MISSING_PSYCOPG, "runs": None})
    assert row == {"check": "store", "ok": False, "detail": MISSING_PSYCOPG}


def test_store_row_fails_with_the_no_store_yet_message():
    row = _store_row_for({"kind": "sqlite", "reachable": False,
                          "error": "no store yet (it is created by the first run)", "runs": None})
    assert row["ok"] is False
    assert row["detail"] == "no store yet (it is created by the first run)"


def test_store_row_is_ok_with_no_runs_yet_for_a_fresh_postgres_store():
    row = _store_row_for({"kind": "postgresql", "reachable": False, "runs": None, "fresh": True,
                          "error": "the store answers but has no runs table yet (it is created by the first run)"})
    assert row == {"check": "store", "ok": True, "detail": "postgresql, no runs yet (the first run creates it)"}


def test_store_row_is_ok_with_no_runs_yet_for_a_fresh_sqlite_store():
    row = _store_row_for({"kind": "sqlite", "reachable": False, "runs": None, "fresh": True,
                          "error": "no store yet (it is created by the first run)"})
    assert row == {"check": "store", "ok": True, "detail": "sqlite, no runs yet (the first run creates it)"}


def test_store_row_still_fails_for_an_unreachable_store_that_is_not_fresh():
    row = _store_row_for({"kind": "sqlite", "reachable": False, "runs": None, "fresh": False,
                          "error": "the store did not answer (OperationalError)"})
    assert row == {"check": "store", "ok": False, "detail": "the store did not answer (OperationalError)"}


def test_store_row_cascades_with_the_profile_and_reads_not_checked_when_ungathered():
    assert _rows_by_check(doctor.checks(_good_facts() | {"profile_text": None}))["store"]["detail"] == "skipped: no profile"
    facts = {k: v for k, v in _good_facts().items() if k != "store"}
    assert _rows_by_check(doctor.checks(facts))["store"]["detail"] == "not checked"


def _store_profile(tmp_path, provider_text):
    (tmp_path / "workspace" / "runs").mkdir(parents=True)
    provider = tmp_path / "provider.yaml"
    provider.write_text(provider_text)
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"team: acme\nprovider_profile: {provider}\nworkspace_dir: {tmp_path / 'workspace'}\n")
    return profile


def test_gather_reports_the_run_count_of_a_sqlite_store_and_never_the_url(tmp_path):
    import sqlite3

    from agent_tools.cli import _gather_doctor_facts
    profile = _store_profile(tmp_path, "command: x\n")
    db = tmp_path / "workspace" / "runs" / "cox.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE runs (id TEXT)")
    conn.executemany("INSERT INTO runs VALUES (?)", [("a",), ("b",), ("c",)])
    conn.commit()
    conn.close()
    store = _gather_doctor_facts(profile, tmp_path)["store"]
    assert store == {"kind": "sqlite", "reachable": True, "error": None, "runs": 3, "fresh": False}
    assert "sqlite:" not in str(store) and str(db) not in str(store)


def test_gather_reports_no_store_yet_without_creating_the_file(tmp_path):
    from agent_tools.cli import _gather_doctor_facts
    profile = _store_profile(tmp_path, "command: x\n")
    store = _gather_doctor_facts(profile, tmp_path)["store"]
    assert store == {"kind": "sqlite", "reachable": False,
                     "error": "no store yet (it is created by the first run)", "runs": None, "fresh": True}
    assert not (tmp_path / "workspace" / "runs" / "cox.db").exists()


def test_gather_reports_a_postgres_store_without_the_driver_as_unreachable(tmp_path, monkeypatch):
    import sys

    from agent_tools.cli import _gather_doctor_facts
    from agent_tools.store_dialect import MISSING_PSYCOPG
    monkeypatch.setitem(sys.modules, "psycopg", None)
    url = "postgresql://user:secret@db.example:5432/cox"
    profile = _store_profile(tmp_path, f"command: x\nstorage_url: {url}\n")
    store = _gather_doctor_facts(profile, tmp_path)["store"]
    assert store == {"kind": "postgresql", "reachable": False, "error": MISSING_PSYCOPG, "runs": None, "fresh": False}
    assert "secret" not in str(store)


def test_gather_keeps_every_fragment_of_the_url_out_of_a_driver_error(tmp_path, monkeypatch):
    from agent_tools import store_dialect
    from agent_tools.cli import _gather_doctor_facts
    seen = []

    def leaky_connect(url):
        seen.append(url)
        raise OSError('invalid percent-encoded token: "s%ZZecret" for user "dbuser" at host "db.example"')

    monkeypatch.setattr(store_dialect, "connect_readonly_url", leaky_connect)
    url = "postgresql://dbuser:s%ZZecret@db.example:5432/cox?sslmode=require"
    profile = _store_profile(tmp_path, f"command: x\nstorage_url: {url}\n")
    store = _gather_doctor_facts(profile, tmp_path)["store"]
    assert store == {"kind": "postgresql", "reachable": False,
                     "error": "the store did not answer (OSError)", "runs": None, "fresh": False}
    assert all(fragment not in str(store) for fragment in ("ZZecret", "dbuser", "db.example", "sslmode", "5432"))
    assert seen == [url + "&connect_timeout=5"]


def test_connect_timeout_is_added_once_and_an_explicit_one_is_kept():
    from agent_tools.cli import _with_connect_timeout
    assert _with_connect_timeout("postgresql://u@h/db") == "postgresql://u@h/db?connect_timeout=5"
    assert _with_connect_timeout("postgresql://u@h/db?connect_timeout=30") == "postgresql://u@h/db?connect_timeout=30"


def test_gather_names_an_unknown_scheme_instead_of_calling_it_no_store_yet(tmp_path):
    from agent_tools.cli import _gather_doctor_facts
    profile = _store_profile(tmp_path, "command: x\nstorage_url: postgress://u:pw@h/db\n")
    store = _gather_doctor_facts(profile, tmp_path)["store"]
    assert store["reachable"] is False
    assert store["error"] == "unsupported store scheme postgress: storage_url must be a postgresql:// or sqlite:/// URL"
    assert "pw" not in str(store)


def test_the_no_store_yet_path_agrees_with_the_file_the_reader_opens(tmp_path, monkeypatch):
    import sqlite3

    import pytest

    from agent_tools.cli import _sqlite_store_file
    from agent_tools.store_dialect import connect_readonly_url
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "cox.db"
    urls = (f"sqlite:///{db}", str(db), "sqlite:///cox.db", "cox.db")
    for url in urls:
        assert _sqlite_store_file(url).resolve() == db
        with pytest.raises(sqlite3.OperationalError):
            connect_readonly_url(url)
    sqlite3.connect(db).close()
    for url in urls:
        assert _sqlite_store_file(url).exists()
        connect_readonly_url(url).close()


def test_gather_says_a_store_with_no_runs_table_answers_but_is_empty_of_schema(tmp_path):
    import sqlite3

    from agent_tools.cli import _gather_doctor_facts
    profile = _store_profile(tmp_path, "command: x\n")
    sqlite3.connect(tmp_path / "workspace" / "runs" / "cox.db").close()
    store = _gather_doctor_facts(profile, tmp_path)["store"]
    assert store == {"kind": "sqlite", "reachable": False, "runs": None,
                     "error": "the store answers but has no runs table yet (it is created by the first run)",
                     "fresh": True}


def test_gather_says_the_same_for_a_postgres_server_with_no_runs_table(tmp_path, monkeypatch):
    from agent_tools import store_dialect
    from agent_tools.cli import _gather_doctor_facts

    class UndefinedTable(Exception):
        pass

    def connect(url):
        raise UndefinedTable('relation "runs" does not exist at db.example')

    monkeypatch.setattr(store_dialect, "connect_readonly_url", connect)
    profile = _store_profile(tmp_path, "command: x\nstorage_url: postgresql://u:pw@db.example/cox\n")
    store = _gather_doctor_facts(profile, tmp_path)["store"]
    assert store["error"] == "the store answers but has no runs table yet (it is created by the first run)"
    assert "db.example" not in str(store)


_UNIT = "[Unit]\nDescription=x\n\n[Service]\nExecStart=/opt/bin/cox chair run\nEnvironment=PATH={path}\nRestart=on-failure\n"


def _unit_row(text, tools=None):
    facts = {**_good_facts(), "chair_unit_text": text}
    if tools is not None:
        facts["chair_unit_tools"] = tools
    return _rows_by_check(doctor.checks(facts))["chair unit PATH"]


def test_unit_path_entries_reads_the_environment_path_line():
    assert doctor.unit_path_entries(_UNIT.format(path="/opt/bin:/usr/bin")) == ["/opt/bin", "/usr/bin"]
    assert doctor.unit_path_entries("[Service]\nExecStart=/x\n") is None


def test_a_unit_path_with_all_four_tools_passes():
    row = _unit_row(_UNIT.format(path="/opt/bin:/usr/bin"),
                    {"/opt/bin": ["cox", "claude"], "/usr/bin": ["uv", "pytest"]})
    assert row == {"check": "chair unit PATH", "ok": True, "detail": "2 entries"}


def test_a_unit_path_with_every_tool_in_one_entry_passes():
    row = _unit_row(_UNIT.format(path="/opt/bin"), {"/opt/bin": ["cox", "claude", "uv", "pytest"]})
    assert row["ok"] is True


def test_a_unit_path_without_cox_fails_naming_cox():
    row = _unit_row(_UNIT.format(path="/opt/bin"), {"/opt/bin": ["claude", "uv", "pytest"]})
    assert row == {"check": "chair unit PATH", "ok": False, "detail": "not found on the unit PATH: cox"}


def test_a_unit_path_without_claude_fails_naming_claude():
    row = _unit_row(_UNIT.format(path="/opt/bin"), {"/opt/bin": ["cox", "uv", "pytest"]})
    assert row["ok"] is False
    assert "claude" in row["detail"]


def test_a_unit_path_without_uv_fails_naming_uv():
    row = _unit_row(_UNIT.format(path="/opt/bin"), {"/opt/bin": ["cox", "claude", "pytest"]})
    assert row["ok"] is False
    assert "uv" in row["detail"]


def test_a_unit_path_without_pytest_fails_naming_pytest():
    row = _unit_row(_UNIT.format(path="/opt/bin"), {"/opt/bin": ["cox", "claude", "uv"]})
    assert row == {"check": "chair unit PATH", "ok": False, "detail": "not found on the unit PATH: pytest"}


def test_two_missing_tools_are_named_in_tuple_order():
    row = _unit_row(_UNIT.format(path="/opt/bin"), {"/opt/bin": ["cox", "uv"]})
    assert row == {"check": "chair unit PATH", "ok": False, "detail": "not found on the unit PATH: claude, pytest"}
    row = _unit_row(_UNIT.format(path="/opt/bin"), {"/opt/bin": ["claude", "pytest"]})
    assert row == {"check": "chair unit PATH", "ok": False, "detail": "not found on the unit PATH: cox, uv"}


def test_a_unit_with_no_path_line_fails_naming_the_line():
    row = _unit_row("[Service]\nExecStart=/x\n")
    assert row == {"check": "chair unit PATH", "ok": False, "detail": "no Environment=PATH= line in the unit"}


def test_a_tilde_entry_fails_naming_it_even_when_the_tools_are_found():
    row = _unit_row(_UNIT.format(path="~/.local/bin:/usr/bin"), {"/usr/bin": ["cox", "pytest"]})
    assert row["ok"] is False
    assert "'~/.local/bin'" in row["detail"]


def test_a_percent_or_relative_entry_fails_naming_it():
    assert "'%h/bin'" in _unit_row(_UNIT.format(path="%h/bin:/usr/bin"))["detail"]
    assert "'bin'" in _unit_row(_UNIT.format(path="bin:/usr/bin"))["detail"]


def test_a_unit_that_is_not_installed_passes():
    assert _unit_row(None) == {"check": "chair unit PATH", "ok": True, "detail": "not installed"}


def test_a_unit_fact_never_gathered_passes():
    rows = _rows_by_check(doctor.checks(_good_facts()))
    assert rows["chair unit PATH"] == {"check": "chair unit PATH", "ok": True, "detail": "not gathered"}


def test_a_bare_path_store_reads_as_other_and_still_answers(tmp_path):
    import sqlite3

    from agent_tools.cli import _gather_doctor_facts
    db = tmp_path / "bare.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE runs (id TEXT)")
    conn.commit()
    conn.close()
    profile = _store_profile(tmp_path, f"command: x\nstorage_url: {db}\n")
    assert _gather_doctor_facts(profile, tmp_path)["store"] == {"kind": "other", "reachable": True, "error": None, "runs": 0, "fresh": False}


_PROFILE = {"team": "example-team", "tiers": {"fast": "model-a"}}
_CATALOG = {"model-a": {"released": "2025-01-01"}}
_STALE = "tier fast pins model-a, newer model-b is available"


def test_a_fresh_profile_gives_no_freshness_rows():
    assert doctor.freshness_rows(_PROFILE, _CATALOG, ["model-a"], lambda p, c, v: []) == []


def test_a_stale_tier_gives_one_warning_row_and_leaves_exit_code_unchanged():
    base = doctor.checks(_good_facts())
    rows = doctor.freshness_rows(_PROFILE, _CATALOG, ["model-a"], lambda p, c, v: [_STALE])
    assert rows == [{"check": "model freshness", "ok": True, "warn": True, "detail": _STALE}]
    assert doctor.exit_code(base + rows) == doctor.exit_code(base) == 0
    failing = doctor.checks({})
    assert doctor.exit_code(failing + rows) == doctor.exit_code(failing) == 1


def test_a_none_freshness_fn_gives_one_skip_row_and_leaves_exit_code_unchanged():
    base = doctor.checks(_good_facts())
    rows = doctor.freshness_rows(_PROFILE, _CATALOG, None, None)
    assert len(rows) == 1
    assert rows[0]["detail"] == "skipped: cartridges core too old for the model freshness check"
    assert doctor.exit_code(base + rows) == 0


def test_freshness_fn_gets_vendor_ids_only_when_given():
    calls = []

    def stub(*args):
        calls.append(args)
        return []

    doctor.freshness_rows(_PROFILE, _CATALOG, None, stub)
    doctor.freshness_rows(_PROFILE, _CATALOG, ["model-a"], stub)
    assert calls == [(_PROFILE, _CATALOG), (_PROFILE, _CATALOG, ["model-a"])]


def test_render_shows_warn_and_counts_warnings_apart_from_ok_and_failing():
    rows = doctor.checks(_good_facts()) + doctor.freshness_rows(_PROFILE, _CATALOG, None, lambda p, c: [_STALE])
    text = doctor.render(rows)
    assert re.search(r"model freshness\s+warn\s+" + re.escape(_STALE), text)
    assert text.splitlines()[-1] == "doctor: 16 ok, 0 failing, 1 warning"


_LOCATIONS = home.resolve({}, "/u", "linux", flag="/h")
_CHECKOUT = "/h/installs/coxswain-cartridges"


_PATHS = {"skills_roots": ("/h/skills", "/h/more"), "provider_profile": "/h/profile.yaml", "harness_dir": "/h/harness"}
_MIGRATED = {"home_record": "/h", "user_home": "/u", **_PATHS}


def _home(listing=None, exists=True, writable=True, legacy=None, in_project=False):
    return doctor.home_rows(_LOCATIONS, listing, exists, writable, legacy, in_project, **_MIGRATED)


def _migrated(**overrides):
    return doctor.home_rows(_LOCATIONS, None, True, True, None, False, **{**_MIGRATED, **overrides})


def test_home_rows_come_in_a_fixed_order():
    rows = _home({"coxswain-cartridges": _CHECKOUT})
    assert [r["check"] for r in rows] == [
        "home root",
        "marketplace registered",
        "marketplace location",
        "old layout",
        "home paths",
        "home recorded",
    ]


def test_home_root_ok_when_it_exists_and_is_writable():
    row = _home()[0]
    assert (row["ok"], row["detail"]) == (True, "/h exists and is writable")


def test_home_root_fails_when_missing():
    row = _home(exists=False, writable=False)[0]
    assert (row["ok"], row["detail"]) == (False, "/h does not exist; run `cox setup install` to create it")


def test_home_root_fails_when_not_writable():
    row = _home(writable=False)[0]
    assert (row["ok"], row["detail"]) == (False, "/h exists but is not writable by this user")


def test_marketplace_registered_ok_when_listed():
    assert _home({"coxswain-cartridges": _CHECKOUT})[1]["ok"] is True


def test_marketplace_missing_from_the_listing_fails_registered_and_location():
    rows = _home({"other": "/x"})
    assert [(r["ok"], r["detail"]) for r in rows[1:3]] == [
        (False, "coxswain-cartridges is not in the claude marketplace list; run `cox setup install`"),
        (False, "no coxswain-cartridges entry to compare with the home"),
    ]


def test_claude_absent_warns_on_both_marketplace_rows_without_failing():
    rows = _home(None)
    assert [(r["ok"], r["warn"], r["detail"]) for r in rows[1:3]] == [
        (True, True, "not checked: claude is not on PATH"),
        (True, True, "not checked: claude is not on PATH"),
    ]
    assert doctor.exit_code(rows[1:3]) == 0


def test_home_paths_pass_when_every_path_is_under_the_home():
    row = _home()[4]
    assert (row["check"], row["ok"]) == ("home paths", True)


def test_home_paths_pass_after_tilde_expansion():
    row = _migrated(user_home="/h", skills_roots=("~/skills",), provider_profile="~/p.yaml", harness_dir="~")[4]
    assert row["ok"] is False and row["detail"] == "~ is not under /h"
    row = _migrated(user_home="/h", skills_roots=("~/skills",), provider_profile="~/p.yaml", harness_dir="~/harness")[4]
    assert (row["check"], row["ok"]) == ("home paths", True)


def test_home_paths_fail_naming_the_first_offender():
    row = _migrated(skills_roots=("/h/skills", "/elsewhere/a", "/elsewhere/b"), harness_dir="/also/bad")[4]
    assert (row["ok"], row["detail"]) == (False, "/elsewhere/a is not under /h")


def test_home_recorded_passes_when_the_record_equals_the_home():
    row = _home()[5]
    assert (row["check"], row["ok"]) == ("home recorded", True)


def test_home_recorded_absent_reads_absent_and_fails():
    row = doctor._home_recorded_row(_LOCATIONS, None)
    assert (row["ok"], row["detail"]) == (False, "home record is absent; expected /h")


def test_home_recorded_different_fails_naming_both():
    row = _migrated(home_record="/old")[5]
    assert (row["ok"], row["detail"]) == (False, "home record is /old; expected /h")


def test_unmigrated_machine_warns_on_four_rows_and_produces_no_failure():
    rows = doctor.home_rows(_LOCATIONS, {"coxswain-cartridges": _CHECKOUT}, True, True, "/u/old", False)
    assert [r["check"] for r in rows] == [
        "home root",
        "marketplace registered",
        "marketplace location",
        "old layout",
        "home paths",
        "home recorded",
        "cartridge links",
    ]
    assert [(r["ok"], r["warn"], r["detail"]) for r in rows[3:]] == [(True, True, "not migrated: run cox setup migrate")] * 4
    assert doctor.exit_code(rows) == 0


def test_unmigrated_machine_warns_instead_of_failing_on_the_home_root_marketplace_and_install_bins():
    bins = {"graphs venv": None, "tools venv": None, "tools receipt": "/u/repos/coxswain-tools", "cartridges receipt": None}
    rows = doctor.home_rows(
        _LOCATIONS, {"coxswain-cartridges": "/u/proj/coxswain-cartridges"}, False, False, "/u/old", True, None, bins)
    by = {r["check"]: r for r in rows}
    for check in ("home root", "marketplace location", "install bins"):
        assert (by[check]["ok"], by[check]["warn"]) == (True, True)
        assert by[check]["detail"].startswith("not migrated: run cox setup migrate; ")
    assert doctor.exit_code(rows) == 0


def test_marketplace_location_ok_when_source_equals_the_checkout_under_the_home():
    row = _home({"coxswain-cartridges": _CHECKOUT})[2]
    assert (row["ok"], row["detail"]) == (True, f"{_CHECKOUT} equals the checkout under the home")


def test_marketplace_location_elsewhere_fails_naming_both_paths():
    row = _home({"coxswain-cartridges": "/old/coxswain-cartridges"})[2]
    assert (row["ok"], row["detail"]) == (
        False, f"/old/coxswain-cartridges is not the checkout under the home; expected {_CHECKOUT}")


def test_marketplace_location_in_a_project_folder_fails_and_says_so():
    row = _home({"coxswain-cartridges": "/u/proj/coxswain-cartridges"}, in_project=True)[2]
    assert (row["ok"], row["detail"]) == (
        False, f"/u/proj/coxswain-cartridges is inside a user project folder; expected {_CHECKOUT}")


def test_marketplace_location_compares_text_without_following_links():
    row = _home({"coxswain-cartridges": "/h/installs/../installs/coxswain-cartridges"})[2]
    assert row["ok"] is False


def test_legacy_layout_present_is_a_warn_naming_setup_migrate():
    row = _home(legacy="/u/repos/coxswain-cartridges")[3]
    assert (row["ok"], row["warn"]) == (True, True)
    assert row["detail"] == "old layout still present at /u/repos/coxswain-cartridges; run `cox setup migrate` to move it"
    assert doctor.exit_code([row]) == 0


def test_legacy_layout_absent_is_ok_without_warn():
    row = _home()[3]
    assert (row["ok"], row.get("warn"), row["detail"]) == (True, None, "no old-layout checkout found")


_LINK_BASE, _LINK_LOCAL = "/u/ws/cartridges/base", "/u/ws/cartridges/local"
_ROOT_LINK = "../../../h/installs/coxswain-cartridges"


def _links_row(links):
    return doctor.home_rows(_LOCATIONS, None, True, True, None, False, links, **_MIGRATED)[-1]


def test_cartridge_links_row_is_absent_until_links_are_gathered():
    assert "cartridge links" not in [r["check"] for r in _home()]


def test_cartridge_links_pass_an_absolute_target_equal_to_the_entry_under_the_home():
    row = _links_row({_LINK_BASE: f"{_CHECKOUT}/cartridges/base", _LINK_LOCAL: f"{_CHECKOUT}/cartridges/local"})
    assert (row["check"], row["ok"]) == ("cartridge links", True)


def test_cartridge_links_fail_an_absolute_target_for_another_entry():
    row = _links_row({_LINK_BASE: f"{_CHECKOUT}/cartridges/local"})
    assert row["ok"] is False


def test_cartridge_links_fail_when_they_point_at_the_checkout_root():
    row = _links_row({_LINK_BASE: _ROOT_LINK, _LINK_LOCAL: _ROOT_LINK})
    assert (row["check"], row["ok"]) == ("cartridge links", False)
    assert row["detail"] == (
        f"{_LINK_BASE} -> {_ROOT_LINK}; expected {_ROOT_LINK}/cartridges/base; "
        f"{_LINK_LOCAL} -> {_ROOT_LINK}; expected {_ROOT_LINK}/cartridges/local"
    )


def test_cartridge_links_pass_when_they_point_at_the_subfolders():
    row = _links_row({_LINK_BASE: f"{_ROOT_LINK}/cartridges/base", _LINK_LOCAL: f"{_ROOT_LINK}/cartridges/local"})
    assert (row["check"], row["ok"], row["detail"]) == (
        "cartridge links",
        True,
        "2 links reach their subfolder of the checkout under the home",
    )


def test_cartridge_links_compare_text_without_following_links():
    row = _links_row(
        {
            _LINK_BASE: f"{_ROOT_LINK}/cartridges/base",
            _LINK_LOCAL: "../../../h/installs/../installs/coxswain-cartridges/cartridges/local",
        }
    )
    assert row["ok"] is False and _LINK_BASE not in row["detail"]


def test_cartridge_links_fail_when_none_were_found():
    assert _links_row({})["ok"] is False


_BIN_GRAPHS, _BIN_TOOLS = "/h/installs/coxswain-graphs", "/h/installs/coxswain-tools"
_BINS = {
    "graphs venv": _BIN_GRAPHS,
    "tools venv": _BIN_TOOLS,
    "tools receipt": _BIN_TOOLS,
    "cartridges receipt": _CHECKOUT,
}


def _bins_row(sources):
    return doctor.home_rows(_LOCATIONS, None, True, True, None, False, install_bins=sources, **_MIGRATED)[-1]


def test_install_bins_row_is_absent_until_sources_are_gathered():
    assert "install bins" not in [r["check"] for r in _home()]


def test_install_bins_row_comes_last_after_cartridge_links():
    rows = doctor.home_rows(_LOCATIONS, None, True, True, None, False, {}, _BINS)
    assert [r["check"] for r in rows][-2:] == ["cartridge links", "install bins"]


def test_install_bins_pass_when_each_source_equals_its_own_checkout():
    row = _bins_row(_BINS)
    assert (row["check"], row["ok"], row["detail"]) == (
        "install bins",
        True,
        "all 4 sources reach their checkout under the home",
    )


def test_install_bins_each_source_fails_alone_when_stale():
    for name in _BINS:
        row = _bins_row(dict(_BINS, **{name: "/old/checkout"}))
        assert (row["ok"], row["detail"]) == (False, f"{name} -> /old/checkout; expected {_BINS[name]}")


def test_install_bins_fail_when_a_source_points_at_another_repos_checkout():
    row = _bins_row(dict(_BINS, **{"tools receipt": _CHECKOUT}))
    assert (row["ok"], row["detail"]) == (False, f"tools receipt -> {_CHECKOUT}; expected {_BIN_TOOLS}")


def test_install_bins_compare_text_without_following_links():
    row = _bins_row(dict(_BINS, **{"graphs venv": "/h/installs/../installs/coxswain-graphs"}))
    assert row["ok"] is False and row["detail"].startswith("graphs venv -> ")


def test_install_bins_fail_naming_a_missing_receipt():
    row = _bins_row(dict(_BINS, **{"tools receipt": None}))
    assert (row["ok"], row["detail"]) == (False, "missing tools receipt")


def test_install_bins_fail_when_a_key_is_absent_from_the_mapping():
    row = _bins_row({k: v for k, v in _BINS.items() if k != "cartridges receipt"})
    assert (row["ok"], row["detail"]) == (False, "missing cartridges receipt")


def test_install_bins_name_every_missing_source_in_order():
    row = _bins_row({"tools venv": _BIN_TOOLS, "cartridges receipt": _CHECKOUT})
    assert row["detail"] == "missing graphs venv, tools receipt"


def test_install_bins_report_a_stale_source_beside_a_missing_one():
    row = _bins_row(dict(_BINS, **{"tools receipt": None, "graphs venv": "/old/graphs"}))
    assert row["detail"] == f"missing tools receipt; graphs venv -> /old/graphs; expected {_BIN_GRAPHS}"
