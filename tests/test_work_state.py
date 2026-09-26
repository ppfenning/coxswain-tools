from agent_tools import work_state
from agent_tools.work_state import work_state_mode, work_state_mode_at


def test_the_exact_string_store_selects_store_mode():
    assert work_state_mode({"work_state": "store"}) == "store"


def test_the_string_files_selects_files_mode():
    assert work_state_mode({"work_state": "files"}) == "files"


def test_a_missing_key_selects_files_mode():
    assert work_state_mode({}) == "files"


def test_a_null_selects_files_mode():
    assert work_state_mode({"work_state": None}) == "files"


def test_an_unknown_value_selects_files_mode():
    assert work_state_mode({"work_state": "Store"}) == "files"


def test_the_edge_returns_the_mode_of_the_profile_it_reads(monkeypatch):
    monkeypatch.setattr(work_state, "read_provider_profile", lambda path: {"work_state": "store"})
    assert work_state_mode_at("any/path.yaml") == "store"


def test_the_edge_yields_files_when_the_profile_reads_as_empty(monkeypatch):
    monkeypatch.setattr(work_state, "read_provider_profile", lambda path: {})
    assert work_state_mode_at("any/path.yaml") == "files"
