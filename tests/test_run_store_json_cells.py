from agent_tools.run_store import _SAME, _detail_of, _json_cell, _manifest_record, call_from_row


def _row(**over):
    base = dict.fromkeys(_SAME) | {"call_id": "c1", "model_alias": "m", "ok": 1, "decision_json": None, "detail_json": None}
    return base | over


def test_call_from_row_keeps_a_decoded_decision_dict():
    assert call_from_row(_row(decision_json={"verdict": "ship"}))["decision"] == {"verdict": "ship"}


def test_call_from_row_decodes_a_text_decision():
    assert call_from_row(_row(decision_json='{"verdict": "ship"}'))["decision"] == {"verdict": "ship"}


def test_detail_of_keeps_summary_from_a_dict_detail():
    assert _detail_of(_row(detail_json={"summary": "did it"})) == {"summary": "did it"}


def test_manifest_record_reads_a_decoded_dict():
    assert _manifest_record({"manifest_record": {"a": 1}}) == {"a": 1}


def test_json_cell_decodes_text():
    assert _json_cell('{"a": 1}') == {"a": 1}


def test_json_cell_is_none_for_text_that_is_not_json():
    assert _json_cell("not json") is None


def test_json_cell_passes_a_decoded_value_through():
    assert _json_cell([1]) == [1]


def test_json_cell_is_none_for_bytes_that_are_not_utf8():
    assert _json_cell(bytes([255])) is None
