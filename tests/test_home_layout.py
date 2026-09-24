import dataclasses

from agent_tools.home_layout import DEFAULT, MIN_HEIGHT, PANELS, WEIGHT_MAX, Layout, from_json, rects, step, to_json


def test_rects_cover_the_terminal_with_no_gap_and_no_overlap():
    placed = list(rects(DEFAULT, 80, 40).values())
    assert [row for row, *_ in placed] == [0, 6, 12, 18, 24]
    assert sum(h for *_, h in placed) == 40
    assert {(col, w) for _, col, w, _ in placed} == {(0, 80)}


def test_a_hidden_panel_gets_no_rect_and_its_rows_go_to_the_others():
    placed = rects(dataclasses.replace(DEFAULT, hidden=frozenset({"leader"})), 80, 40)
    assert "leader" not in placed
    assert sum(h for *_, h in placed.values()) == 40


def test_moving_the_focused_panel_later_reorders():
    assert step(DEFAULT, ">", focus="regatta").order == ("leader", "regatta", "backlog", "window", "runs")


def test_growing_runs_stops_at_the_bound_and_no_panel_falls_below_its_minimum():
    grown = DEFAULT
    for _ in range(30):
        grown = step(grown, "+")
    assert grown.runs_weight == WEIGHT_MAX
    assert min(h for *_, h in rects(grown, 80, 40).values()) >= MIN_HEIGHT


def test_a_digit_toggles_its_panel_and_an_unknown_key_changes_nothing():
    assert step(DEFAULT, "2").hidden == frozenset({PANELS[1]})
    assert step(DEFAULT, "x") is DEFAULT


def test_garbage_yields_the_default_and_a_layout_round_trips():
    assert from_json({"order": "nonsense", "hidden": 3}) == DEFAULT
    moved = Layout(tuple(reversed(PANELS)), frozenset({"window"}), 0.5)
    assert from_json(to_json(moved)) == moved
