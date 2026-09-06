from agent_tools.panel import Span, box, meter


def _text(line):
    return "".join(span.text for span in line)


def test_exact_height_and_width_for_a_short_body():
    body = ((Span("holder: cox-loop-22308f6f"),), (Span("status: live"),))
    panel = box("LEADER", body, width=30, height=5)
    assert len(panel) == 5
    assert all(len(_text(line)) == 30 for line in panel)


def test_clipping_a_long_body_line_ends_in_ellipsis():
    body = ((Span("x" * 40),),)
    panel = box("T", body, width=10, height=3)
    row = _text(panel[1])
    assert len(row) == 10
    assert row[-2] == "…"


def test_padding_for_a_short_body_adds_blank_rows():
    body = ((Span("only"),),)
    panel = box("T", body, width=10, height=4)
    assert len(panel) == 4
    assert _text(panel[2]) == "│" + " " * 8 + "│"


def test_a_titled_panel_narrower_than_the_title_decoration_still_fits_width():
    panel = box("X", ((Span("y"),),), width=4, height=3)
    assert all(len(_text(line)) == 4 for line in panel)


def test_empty_tuple_below_minimum_size():
    assert box("T", (), width=1, height=5) == ()
    assert box("T", (), width=5, height=1) == ()


def test_half_full_meter():
    assert meter(0.5, 10) == (Span("█████     ", "meter"),)


def test_meter_renders_a_partial_block_at_sub_character_resolution():
    assert meter(0.3125, 8) == (Span("██▌     ", "meter"),)


def test_clamped_meter():
    assert meter(5.0, 4) == (Span("████", "meter"),)
    assert meter(-5.0, 4) == (Span("    ", "meter"),)
