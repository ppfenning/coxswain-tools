import json
from pathlib import Path

from agent_tools.card_keys import find_duplicate, find_row, normalized_title, row_keys

ROWS = json.loads((Path(__file__).parent / "fixtures" / "work_item_card_v1.json").read_text())["rows"]


def test_normalized_title_ignores_case_and_punctuation():
    assert normalized_title("Fix: the Thing!") == normalized_title("fix the thing")
    assert normalized_title("  Fix: the Thing! ") == "fix the thing"


def test_row_keys_reads_top_level_keys():
    assert row_keys(ROWS[0]) == (1648, "PVTI_task1", "PVTI_init1")
    assert row_keys(ROWS[2]) == (None, None, None)


def test_row_keys_falls_back_to_extra_issue():
    assert row_keys({"extra": {"issue": "1647"}})[0] == 1647
    assert row_keys({"issue_number": None, "extra": {"issue": 12}})[0] == 12
    assert row_keys({"extra": {"issue": "abc"}})[0] is None


def test_find_row_by_issue_number():
    assert find_row(ROWS, 1647, None) is ROWS[1]


def test_find_row_by_project_item_id_when_issue_is_none():
    assert find_row(ROWS, None, "PVTI_task1") is ROWS[0]
    assert find_row(ROWS, None, "") is None
    assert find_row(ROWS, 9999, None) is None


def test_find_duplicate():
    open_issues = [{"number": 7, "title": "fix the Thing"}]
    assert find_duplicate("Fix: the thing", open_issues) == 7
    assert find_duplicate("Add a widget", open_issues) is None
    assert find_duplicate("!!!", [{"number": 8, "title": "???"}]) is None
