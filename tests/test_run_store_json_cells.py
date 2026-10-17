from agent_tools.run_store import call_from_row

# The columns call_from_row copies unchanged from a node_calls row.
COLUMNS = (
    "role", "task_id", "tier", "cost_usd", "ceiling_usd", "ceiling_source", "turns", "duration_ms",
    "input_tokens", "cache_read_tokens", "cache_creation_tokens", "input_total", "output_tokens", "ts",
)


def _row(**over):
    base = dict.fromkeys(COLUMNS) | {"call_id": "c1", "model_alias": "m", "ok": 1, "decision_json": None, "detail_json": None}
    return base | over


def test_call_from_row_keeps_a_decoded_decision_dict():
    assert call_from_row(_row(decision_json={"verdict": "ship"}))["decision"] == {"verdict": "ship"}


def test_call_from_row_decodes_a_text_decision():
    assert call_from_row(_row(decision_json='{"verdict": "ship"}'))["decision"] == {"verdict": "ship"}


def test_call_from_row_gives_none_for_junk_decision_text():
    assert call_from_row(_row(decision_json="not json"))["decision"] is None


def test_call_from_row_gives_none_for_non_utf8_decision_bytes():
    assert call_from_row(_row(decision_json=bytes([255])))["decision"] is None
