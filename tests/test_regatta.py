from dataclasses import dataclass

from agent_tools.regatta import STATUS_ROLE, lane, progress_of, regatta
from agent_tools.runs_detail import NODE_ORDER


@dataclass(frozen=True)
class _Row:
    run: str
    node: str
    status: str


def test_progress_of_first_node_is_zero():
    assert progress_of(_Row("r1", NODE_ORDER[0], "running")) == 0.0


def test_progress_of_last_node_is_one():
    assert progress_of(_Row("r1", NODE_ORDER[-1], "running")) == 1.0


def test_progress_of_with_no_node_yet_is_zero():
    assert progress_of(_Row("r1", "", "running")) == 0.0


def test_lane_at_zero_progress_sits_at_column_zero():
    line = lane("r1", 0.0, "running", 20, 0)
    assert line[0].role == "ok"


def test_lane_at_one_progress_touches_last_column():
    line = lane("r1", 1.0, "running", 20, 0)
    assert line[-1].role == "ok"


def test_lane_two_ticks_differ_while_running():
    a = lane("r1", 0.5, "running", 20, 0)
    b = lane("r1", 0.5, "running", 20, 1)
    assert a != b


def test_lane_two_ticks_identical_while_exited():
    a = lane("r1", 0.5, "exited", 20, 0)
    b = lane("r1", 0.5, "exited", 20, 1)
    assert a == b


def test_lane_orphaned_boat_carries_alert():
    line = lane("r1", 0.5, "orphaned", 20, 0)
    assert any(s.role == "alert" for s in line)


def test_lane_is_exactly_width_columns():
    line = lane("r1", 0.4, "running", 30, 3)
    assert sum(len(s.text) for s in line) == 30


def test_a_stalled_lane_is_warn_and_its_hull_still_animates():
    assert STATUS_ROLE["stalled"] == "warn"
    first, second = lane("r1", 0.5, "stalled", 30, 0), lane("r1", 0.5, "stalled", 30, 1)
    hull = lambda line: next(s for s in line if s.role == "warn")  # noqa: E731
    assert hull(first).text != hull(second).text


def test_regatta_returns_finish_line_plus_one_lane_per_row():
    rows = (_Row("r1", NODE_ORDER[0], "running"), _Row("r2", NODE_ORDER[-1], "exited"))
    result = regatta(rows, 20, 0)
    assert len(result) == 3
