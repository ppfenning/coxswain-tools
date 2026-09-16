from agent_tools import schema


def test_cell_returns_the_version_when_present():
    assert schema.cell("1.0") == "1.0"


def test_cell_returns_a_question_mark_for_none():
    assert schema.cell(None) == "?"


def test_status_is_ok_when_every_known_major_agrees():
    state, detail = schema.status({"cartridges": "1.0", "graphs": "1.0", "tools": "1.0"})
    assert (state, detail) == ("ok", "cartridges 1.0, graphs 1.0, tools 1.0")


def test_status_warns_naming_only_the_disagreeing_component():
    state, detail = schema.status({"cartridges": "1.0", "graphs": "2.0", "tools": "1.0"})
    assert (state, detail) == ("WARN", "graphs 2.0")


def test_a_none_entry_never_triggers_a_warn_and_never_appears_in_either_message():
    state, detail = schema.status({"cartridges": "1.0", "graphs": None, "tools": "1.0"})
    assert state == "ok"
    assert "graphs" not in detail
    assert detail == "cartridges 1.0, tools 1.0"
