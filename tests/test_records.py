import json
from agent_tools import records

USAGE = {"run_id": "r", "calls": [
    {"role": "build", "model": "sonnet", "turns": 40, "cost_usd": 0.5, "input_tokens": 100, "cache_read_tokens": 900, "input_total": 1000},
    {"role": "review_charter", "model": "opus", "turns": 8, "cost_usd": 0.3, "input_tokens": 50, "cache_read_tokens": 450, "input_total": 500},
    {"role": "build", "model": "sonnet", "turns": 20, "cost_usd": 0.2, "input_tokens": 1000},
]}


def test_usage_summary_totals_and_orders_by_cost():
    s = records.usage_summary(USAGE)
    assert s["calls"] == 3 and s["cost_usd"] == 1.0 and s["turns"] == 68
    assert list(s["by_role"]) == ["build", "review_charter"] and s["by_role"]["build"]["calls"] == 2
    assert s["by_model"]["opus"]["cost_usd"] == 0.3
    assert s["input_total"] == 2500 and s["cache_read_share"] == 0.54
    assert s["most_expensive"][0]["cost_usd"] == 0.5


def test_trace_summary_counts_tools_reads_and_commands(tmp_path):
    events = [
        {"type": "system"},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "/r/a.py", "offset": 1, "limit": 40}}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "/r/b.py"}}, {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}}]}},
        {"type": "result", "num_turns": 3, "total_cost_usd": 0.12, "subtype": "success"},
    ]
    p = tmp_path / "build-1.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in events) + "\nnot json\n")
    s = records.trace_summary(records.load_trace(p))
    assert s["turns"] == 3 and s["cost_usd"] == 0.12 and s["subtype"] == "success" and not s["is_error"]
    assert s["tools"] == {"Read": 2, "Bash": 1} and s["reads"] == {"a.py": 1, "b.py": 1}
    assert s["whole_file_reads"] == 1 and s["commands"] == ["pytest -q"]


def test_format_table_aligns_numbers_right():
    out = records.format_table([{"role": "build", "cost_usd": 1.5, "turns": 40}, {"role": "plan", "cost_usd": 0.25, "turns": 9}], ["role", "cost_usd", "turns"])
    lines = out.splitlines()
    assert lines[0].startswith("role") and lines[1].endswith("40") and lines[2].endswith(" 9")
