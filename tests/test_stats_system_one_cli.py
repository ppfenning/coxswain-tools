import datetime
import json

import pytest

from agent_tools.cli import main


def call(i, *, role="handoff", agreed=True, day=1):
    ts = datetime.datetime(2026, 9, day, tzinfo=datetime.UTC) + datetime.timedelta(minutes=i)
    return {
        "role": role, "ts": ts.isoformat(),
        "decision": {
            "role": role, "model_id": "m", "claude_code_version": "v", "system_one_mode": "shadow",
            "system_one_answer": "no", "system_one_confidence": 0.9, "system_one_threshold": 0.8,
            "system_one_agreed": agreed,
        },
    }


@pytest.fixture
def ws(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "a.usage.json").write_text(json.dumps({"calls": [call(i) for i in range(100)]}))
    (runs / "b.usage.json").write_text(json.dumps({"calls": [call(i, role="review_charter") for i in range(3)]}))
    (runs / "c.usage.json").write_text("{not json")
    profile = tmp_path / "profile.yaml"
    profile.write_text("assume: a\n")
    monkeypatch.setenv("AGENT_TOOLS_PROFILE", str(profile))
    return tmp_path, profile


def run(ws, *extra):
    tmp, _ = ws
    return main(["stats", "system-one", str(tmp / "runs"), "--plans-dir", str(tmp / "plans"), *extra])


def test_report_lists_each_role_with_its_verdict(ws, capsys):
    assert run(ws) == 0
    out = capsys.readouterr().out
    assert "handoff: READY" in out
    assert "review_charter: NOT YET (rows 3 < 100)" in out


def test_role_filters_and_json_is_parseable(ws, capsys):
    assert run(ws, "--role", "handoff", "--json") == 0
    roles = json.loads(capsys.readouterr().out)["roles"]
    assert [r["role"] for r in roles] == ["handoff"]


def test_since_after_every_row_leaves_nothing(ws, capsys):
    assert run(ws, "--since", "2026-10-01") == 0
    assert capsys.readouterr().out.strip() == "no shadow rows in any usage file"


def test_propose_writes_a_file_for_a_ready_role_only_and_never_touches_the_profile(ws, capsys):
    tmp, profile = ws
    before = profile.read_bytes()
    assert run(ws, "--propose") == 0
    written = sorted(p.name for p in (tmp / "plans").iterdir())
    assert len(written) == 1 and written[0].startswith("system-one-graduation-handoff-") and written[0].endswith(".md")
    text = (tmp / "plans" / written[0]).read_text()
    assert "system_one.roles.handoff.mode:  shadow  ->  on" in text
    assert "skipped review_charter: not ready" in capsys.readouterr().err
    assert profile.read_bytes() == before


def test_without_propose_nothing_is_written(ws):
    run(ws)
    assert not (ws[0] / "plans").exists()
