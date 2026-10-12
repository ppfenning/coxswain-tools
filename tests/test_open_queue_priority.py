from agent_tools.open_queue_priority import (
    Row,
    Target,
    format_listing,
    open_queue,
    open_rows,
    target_for_bottom,
    target_for_down,
    target_for_top,
    target_for_up,
)

MIXED = [
    Row("a", "task", "ready", 3),
    Row("a", "task", "done", 10),
    Row("b", "task", "approved", 5),
    Row("b", "task", "dropped", 9),
    Row("c", "task", "quarantined", 1),
    Row("c", "task", "landed", 8),
    Row("d", "intake", "queued", 5),
    Row("d", "intake", "done", 7),
    Row("e", "task", "weird", 6),
]


def test_open_rows_keeps_only_open():
    assert open_rows(MIXED) == [
        Row("a", "task", "ready", 3),
        Row("b", "task", "approved", 5),
        Row("c", "task", "quarantined", 1),
        Row("d", "intake", "queued", 5),
    ]


def test_open_rows_kind_decides_state_set():
    assert open_rows([Row("a", "intake", "ready", 1), Row("a", "task", "queued", 1)]) == []


def test_open_queue_orders_priority_desc_then_id():
    assert open_queue(MIXED + [Row("b", "task", "ready", 2)]) == [(5, "b", 2), (5, "d", 1), (3, "a", 1), (1, "c", 1)]


def test_top_ignores_done_priority_10():
    assert target_for_top(MIXED, "zz") == Target(5, 6)


def test_top_with_nothing_else_open_keeps_current():
    assert target_for_top([Row("a", "task", "ready", 4), Row("z", "task", "done", 10)], "a") == Target(None, 4)


def test_top_already_above_is_idempotent():
    rows = [Row("a", "task", "ready", 6), Row("b", "task", "ready", 5)]
    assert target_for_top(rows, "a") == Target(5, 6)


def test_top_tied_goes_one_above():
    rows = [Row("a", "task", "ready", 5), Row("b", "task", "ready", 5)]
    assert target_for_top(rows, "a") == Target(5, 6)


def test_bottom_ignores_done_priority_10():
    assert target_for_bottom(MIXED, "b") == Target(1, 0)


def test_bottom_can_go_negative():
    rows = [Row("a", "task", "ready", 0), Row("b", "task", "ready", 0)]
    assert target_for_bottom(rows, "a") == Target(0, -1)


def test_bottom_already_below_is_idempotent():
    rows = [Row("a", "task", "ready", -1), Row("b", "task", "ready", 0)]
    assert target_for_bottom(rows, "a") == Target(0, -1)


def test_bottom_with_nothing_else_open_keeps_current():
    assert target_for_bottom([Row("a", "task", "ready", 4)], "a") == Target(None, 4)


def test_up_passes_neighbour():
    rows = [Row("a", "task", "ready", 2), Row("b", "task", "ready", 3), Row("c", "task", "ready", 5)]
    assert target_for_up(rows, "a") == 4


def test_up_with_no_higher_neighbour():
    rows = [Row("a", "task", "ready", 5), Row("b", "task", "ready", 3), Row("c", "task", "done", 9)]
    assert target_for_up(rows, "a") == 6


def test_down_passes_neighbour():
    rows = [Row("a", "task", "ready", 4), Row("b", "task", "ready", 3), Row("c", "task", "ready", 1)]
    assert target_for_down(rows, "a") == 2


def test_down_with_no_lower_neighbour_goes_negative():
    rows = [Row("a", "task", "ready", 0), Row("b", "task", "ready", 3), Row("c", "task", "dropped", -5)]
    assert target_for_down(rows, "a") == -1


def test_current_uses_all_own_rows_including_done():
    rows = [Row("a", "task", "ready", 2), Row("a", "task", "done", 10), Row("b", "task", "ready", 5)]
    assert target_for_up(rows, "a") == 11


def test_unknown_initiative_has_priority_zero():
    rows = [Row("b", "task", "ready", 3), Row("c", "task", "ready", -2)]
    assert target_for_up(rows, "zz") == 4
    assert target_for_down(rows, "zz") == -3
    assert target_for_top(rows, "zz") == Target(3, 4)
    assert target_for_bottom(rows, "zz") == Target(-2, -3)
    assert target_for_up([], "zz") == 1


def test_format_listing_lines():
    assert format_listing(open_queue(MIXED)) == ["5  b  1", "5  d  1", "3  a  1", "1  c  1"]
