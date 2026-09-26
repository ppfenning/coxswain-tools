from agent_tools import route
from agent_tools.chair_read_docket import docket_from_builder, work_store_ready


def _item(id: str, state: str, needs: tuple[str, ...] = (), initiative: str = "alpha", phase: str = "p1") -> dict:
    return {"id": id, "initiative": initiative, "phase": phase, "state": state, "needs": list(needs), "file": f"{phase}/{id}.md"}


def _init(*ready: str) -> dict:
    return {"id": "alpha", "started": False, "ready_tasks": [{"id": t, "needs": []} for t in ready], "landed": set()}


def test_builder_output_maps_to_the_documented_keys():
    items = [_item("t0", "done"), _item("t1", "ready", ("t0",)), _item("t2", "ready", ("t9",)), _item("t3", "todo")]
    assert docket_from_builder(route.initiative_summaries(items), items, 2, 4) == {
        "initiatives": [{"id": "alpha", "started": True, "ready_tasks": [{"id": "t1", "needs": ["t0"]}], "landed": {"t0"}}],
        "busy_lanes": 2,
        "max_in_flight": 4,
    }


def test_a_docket_with_one_ready_task_is_ready():
    assert work_store_ready({"initiatives": [_init(), _init("t1")], "busy_lanes": 0, "max_in_flight": 4}) is True


def test_a_docket_where_every_ready_tasks_list_is_empty_is_not_ready():
    assert work_store_ready({"initiatives": [_init(), _init()], "busy_lanes": 0, "max_in_flight": 4}) is False


def test_an_empty_docket_is_not_ready():
    assert work_store_ready({"initiatives": [], "busy_lanes": 0, "max_in_flight": 4}) is False
