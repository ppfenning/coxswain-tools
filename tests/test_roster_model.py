from agent_tools.roster_model import rows, set_context, set_skills, toggle


def test_rows_reports_seat_layer_skills_count_and_context():
    resolved_cast = {"builder": {"enabled": True, "skills": ["writer", "reviewer"], "context": "ships features"}}
    layers = (("base", {"builder": {}}),)
    row = rows(resolved_cast, layers)[0]
    assert row.seat == "builder"
    assert row.enabled is True
    assert row.layer == "base"
    assert row.skills_count == 2
    assert row.context == "ships features"


def test_rows_defaults_enabled_true_when_the_seat_names_no_flag():
    row = rows({"builder": {"skills": []}}, ())[0]
    assert row.enabled is True


def test_toggle_disables_a_seat_that_reads_as_enabled():
    fragment = {"cast": {"builder": {"enabled": True}}}
    assert toggle(fragment, "builder") == {"cast": {"builder": {"enabled": False}}}


def test_toggle_enables_a_seat_that_reads_as_disabled():
    fragment = {"cast": {"builder": {"enabled": False}}}
    assert toggle(fragment, "builder") == {"cast": {"builder": {"enabled": True}}}


def test_set_skills_writes_the_seats_skills_list():
    assert set_skills("builder", ["writer", "reviewer"]) == {"cast": {"builder": {"skills": ["writer", "reviewer"]}}}


def test_set_context_writes_the_seats_context_line():
    assert set_context("builder", "ships features") == {"cast": {"builder": {"context": "ships features"}}}
