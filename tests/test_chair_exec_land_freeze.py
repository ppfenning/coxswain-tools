import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from agent_tools import chair_exec
from agent_tools.chair_exec import perform
from agent_tools.chair_land_freeze import write_freeze
from tests.test_chair_exec import _deps, _land

NOW = datetime(2026, 10, 17, 19, 12, tzinfo=UTC)


def test_a_freeze_set_between_two_lands_skips_the_second(tmp_path, monkeypatch):
    conn = sqlite3.connect(tmp_path / "cox.db")
    conn.execute(
        "CREATE TABLE leases (name TEXT PRIMARY KEY, holder TEXT, epoch INTEGER, heartbeat_at TEXT, expires_at TEXT)"
    )
    conn.commit()
    conn.close()
    ran: list[str] = []

    def fake_execute(action, deps, blocked):
        ran.append(action["task_id"])
        write_freeze(tmp_path, "release cut", NOW, NOW + timedelta(minutes=30))
        return chair_exec._result(action, "landed")

    monkeypatch.setattr(chair_exec, "_execute", fake_execute)
    deps = replace(_deps([]), runs_dir=tmp_path, now=NOW.isoformat)
    results = perform([_land("t1", "r"), _land("t2", "r")], deps, lambda: 1, False)
    assert ran == ["t1"]
    assert [(r["status"], r["reason"]) for r in results] == [
        ("landed", ""),
        ("skipped", "lands frozen until 19:42 (release cut)"),
    ]
