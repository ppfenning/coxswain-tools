from pathlib import Path

import pytest

from agent_tools import route, run_store
from tests.test_route_decompose_rows import INTAKE, PLAN, Store


def _store(monkeypatch, tmp_path, intake):
    fake = Store([intake])
    monkeypatch.setattr(run_store, "read_queue", fake.read_queue)
    monkeypatch.setattr(run_store, "upsert_row_detail", fake.upsert_row_detail)
    monkeypatch.chdir(tmp_path)
    return fake


def _planned(fake):
    return {(r["kind"], r["task_id"]): r for r in fake.upserts}


@pytest.mark.parametrize(
    "intake",
    [
        {**INTAKE, "priority": 2},
        {**INTAKE, "extra": {"id": "I1", "priority": 2}},
    ],
)
def test_initiative_and_tasks_take_the_intake_priority(monkeypatch, tmp_path, intake):
    fake = _store(monkeypatch, tmp_path, intake)
    assert route.decompose_into_rows(Path("runs"), "I1", lambda data: PLAN)[0] == 0
    rows = _planned(fake)
    assert [rows[k]["priority"] for k in (("initiative", "init-a"), ("task", "t1"), ("task", "t2"))] == [2, 2, 2]
    assert rows[("intake", "I1")].get("priority") == intake.get("priority")


@pytest.mark.parametrize("intake", [INTAKE, {**INTAKE, "priority": 0}])
def test_no_priority_writes_no_priority_key(monkeypatch, tmp_path, intake):
    fake = _store(monkeypatch, tmp_path, intake)
    route.decompose_into_rows(Path("runs"), "I1", lambda data: PLAN)
    assert [r for r in fake.upserts if r["kind"] != "intake" and "priority" in r] == []
