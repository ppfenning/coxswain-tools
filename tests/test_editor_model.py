import functools

from agent_tools.editor_model import (
    Effect,
    Row,
    State,
    apply_effect_result,
    apply_probe,
    apply_text,
    fold_effects,
    frame,
    rows,
    sections,
    step,
)
from agent_tools.provenance import attribute


def test_a_key_set_by_base_is_read_only_with_layer_base():
    probe = {
        "resolved": {"policy": {"review_tier": "2"}},
        "provenance": {"policy.review_tier": "base"},
    }
    row = rows(probe, team="acme")[0]
    assert row.layer == "base"
    assert row.editable is False


def test_a_key_set_by_the_team_is_editable():
    probe = {
        "resolved": {"policy": {"review_tier": "2"}},
        "provenance": {"policy.review_tier": "acme"},
    }
    row = rows(probe, team="acme")[0]
    assert row.layer == "acme"
    assert row.editable is True


def test_a_key_set_by_edited_is_editable():
    probe = {
        "resolved": {"policy": {"review_tier": "2"}},
        "provenance": {"policy.review_tier": "edited"},
    }
    row = rows(probe, team="acme")[0]
    assert row.layer == "edited"
    assert row.editable is True


def test_a_seat_split_across_layers_yields_one_read_only_and_one_editable_row():
    probe = {
        "resolved": {"crew": {"builder": {"enabled": True, "skills": ["writer"]}}},
        "provenance": {"crew.builder.enabled": "base", "crew.builder.skills": "acme"},
    }
    by_key = {row.key: row for row in rows(probe, team="acme")}
    assert by_key["crew.builder.enabled"].editable is False
    assert by_key["crew.builder.enabled"].layer == "base"
    assert by_key["crew.builder.skills"].editable is True
    assert by_key["crew.builder.skills"].layer == "acme"


def test_kinds_are_assigned_by_key():
    probe = {
        "resolved": {
            "crew": {"builder": {"enabled": True, "skills": ["writer"]}},
            "policy": {
                "review_tier": "2",
                "plan_competition": {"min_tier": "1"},
                "build_budget_usd_max": "5",
            },
            "landing_areas": {"checks": ["ci"]},
            "context": ["conventions.md"],
        },
        "provenance": {
            "crew.builder.enabled": "base",
            "crew.builder.skills": "base",
            "policy.review_tier": "base",
            "policy.plan_competition.min_tier": "base",
            "policy.build_budget_usd_max": "base",
            "landing_areas.checks": "base",
            "context": "base",
        },
    }
    kinds = {row.key: row.kind for row in rows(probe, team="acme")}
    assert kinds["crew.builder.enabled"] == "toggle"
    assert kinds["crew.builder.skills"] == "text"
    assert kinds["policy.review_tier"] == "choice"
    assert kinds["policy.plan_competition.min_tier"] == "choice"
    assert kinds["policy.build_budget_usd_max"] == "text"
    assert kinds["landing_areas.checks"] == "text"
    assert kinds["context"] == "text"


def test_a_probe_carrying_a_provenance_error_yields_every_row_read_only_with_layer_unknown():
    probe = {
        "resolved": {"policy": {"review_tier": "2"}},
        "provenance": {"policy.review_tier": "acme"},
        "provenance_error": "cartridge load failed",
    }
    result = rows(probe, team="acme")
    assert result
    assert all(row.layer == "unknown" for row in result)
    assert all(row.editable is False for row in result)


def test_a_seat_field_no_layer_sets_yields_no_row_for_that_field():
    probe = {
        "resolved": {"crew": {"builder": {"enabled": True}}},
        "provenance": {"crew.builder.enabled": "base"},
    }
    keys = {row.key for row in rows(probe, team="acme")}
    assert keys == {"crew.builder.enabled"}


def test_a_seat_under_the_mirrored_cast_key_still_reads_its_crew_value():
    probe = {
        "resolved": {"cast": {"builder": {"enabled": True, "skills": ["writer"]}}},
        "provenance": {"crew.builder.enabled": "acme", "crew.builder.skills": "base"},
    }
    by_key = {row.key: row for row in rows(probe, team="acme")}
    assert by_key["crew.builder.enabled"].value is True
    assert by_key["crew.builder.skills"].value == ["writer"]


def test_a_fixed_key_explicitly_set_to_null_still_yields_a_row():
    probe = {
        "resolved": {"context": None},
        "provenance": {"context": "base"},
    }
    row = rows(probe, team="acme")[0]
    assert row.key == "context"
    assert row.value is None
    assert row.layer == "base"


def test_a_scalar_seat_value_yields_a_single_row_not_split_into_fields():
    probe = {
        "resolved": {"crew": {"reviewer": "codex"}},
        "provenance": {"crew.reviewer": "acme"},
    }
    result = rows(probe, team="acme")
    assert [row.key for row in result] == ["crew.reviewer"]
    assert result[0].value == "codex"
    assert result[0].editable is True


def test_rows_and_provenance_attribute_agree_on_which_keys_are_editable_paths():
    resolved = {
        "policy": {"review_tier": "2"},
        "crew": {"builder": {"enabled": True, "skills": ["writer"]}, "reviewer": "codex"},
        "skills": {"review": {}},
    }
    provenance = attribute([("only", resolved)])
    probe = {"resolved": resolved, "provenance": provenance}
    row_keys = {row.key for row in rows(probe, team="only")}
    assert row_keys == set(provenance.keys())


def test_provenance_output_flows_into_rows_and_a_team_owned_field_is_editable():
    layers = [
        ("base", {"policy": {"review_tier": "1"}}),
        ("acme", {"policy": {"review_tier": "2"}}),
    ]
    probe = {"resolved": layers[-1][1], "provenance": attribute(layers)}
    row = rows(probe, team="acme")[0]
    assert row.layer == "acme"
    assert row.editable is True


def test_a_skills_value_that_is_not_a_mapping_is_skipped_not_leaked_as_a_row():
    probe = {
        "resolved": {"skills": ["review", "writer"]},
        "provenance": {},
    }
    result = rows(probe, team="acme")
    assert not any(row.key.startswith("skills.") for row in result)


def test_a_probe_with_resolved_but_no_provenance_key_treats_rows_as_unknown_and_read_only():
    probe = {"resolved": {"policy": {"review_tier": "2"}}}
    row = rows(probe, team="acme")[0]
    assert row.layer == "unknown"
    assert row.editable is False


_TOGGLE_ROW = Row(key="crew.builder.enabled", value=False, layer="acme", editable=True, kind="toggle")
_READONLY_ROW = Row(key="crew.builder.skills", value="x", layer="base", editable=False, kind="text")
_CHOICE_ROW = Row(
    key="policy.review_tier",
    value="1",
    layer="acme",
    editable=True,
    kind="choice",
    choices=("1", "2", "3"),
)
_TEXT_ROW = Row(key="policy.build_budget_usd_max", value="5", layer="acme", editable=True, kind="text")


def test_step_j_skips_a_read_only_row_between_two_editable_rows():
    state = State(rows=(_TOGGLE_ROW, _READONLY_ROW, _CHOICE_ROW), cursor=0, pending={}, message="")
    after = step(state, "j")
    assert after.cursor == 2


def test_step_j_from_a_read_only_row_moves_forward_to_the_next_editable_row():
    state = State(rows=(_TOGGLE_ROW, _READONLY_ROW, _CHOICE_ROW), cursor=1, pending={}, message="")
    after = step(state, "j")
    assert after.cursor == 2


def test_step_k_from_a_read_only_row_moves_backward_to_the_previous_editable_row():
    state = State(rows=(_TOGGLE_ROW, _READONLY_ROW, _CHOICE_ROW), cursor=1, pending={}, message="")
    after = step(state, "k")
    assert after.cursor == 0


def test_step_j_past_the_last_editable_row_holds_at_that_row():
    state = State(rows=(_TOGGLE_ROW, _CHOICE_ROW, _READONLY_ROW), cursor=2, pending={}, message="")
    after = step(state, "j")
    assert after.cursor == 1


def test_step_space_toggles_a_toggle_row_and_toggling_again_clears_pending():
    state = State(rows=(_TOGGLE_ROW,), cursor=0, pending={}, message="")
    once = step(state, "space")
    assert once.pending == {"crew.builder.enabled": True}
    twice = step(once, "space")
    assert twice.pending == {}


def test_step_space_cycles_a_choice_row_through_its_own_declared_values():
    state = State(rows=(_CHOICE_ROW,), cursor=0, pending={}, message="")
    after = step(state, "space")
    assert after.pending == {_CHOICE_ROW.key: _CHOICE_ROW.choices[1]}


def test_step_space_preserves_an_int_typed_choice_values_type():
    int_choice_row = Row(
        key="policy.review_tier",
        value=1,
        layer="acme",
        editable=True,
        kind="choice",
        choices=("1", "2", "3"),
    )
    state = State(rows=(int_choice_row,), cursor=0, pending={}, message="")
    after = step(state, "space")
    assert after.pending == {"policy.review_tier": 2}
    assert isinstance(after.pending["policy.review_tier"], int)


def test_step_space_on_a_choice_row_clears_pending_after_a_full_cycle():
    state = State(rows=(_CHOICE_ROW,), cursor=0, pending={}, message="")
    final = functools.reduce(lambda s, _: step(s, "space"), range(len(_CHOICE_ROW.choices)), state)
    assert final.pending == {}


def test_step_space_on_a_read_only_row_is_refused_and_leaves_state_unchanged_but_for_message():
    state = State(rows=(_READONLY_ROW,), cursor=0, pending={}, message="")
    after = step(state, "space")
    assert after.rows == state.rows
    assert after.cursor == state.cursor
    assert after.pending == state.pending
    assert after.message != state.message


def test_step_space_on_a_read_only_row_does_not_mutate_the_input_pending_dict():
    state = State(rows=(_READONLY_ROW,), cursor=0, pending={"x": 1}, message="")
    before = dict(state.pending)
    step(state, "space")
    assert state.pending == before


def test_step_e_refuses_a_non_text_row_and_leaves_pending_untouched():
    state = State(rows=(_TOGGLE_ROW,), cursor=0, pending={}, message="")
    after = step(state, "e")
    assert after.pending == {}
    assert after.message == "crew.builder.enabled cannot be edited"


def test_step_e_on_an_editable_text_row_sets_an_editing_message():
    state = State(rows=(_TEXT_ROW,), cursor=0, pending={}, message="")
    after = step(state, "e")
    assert after.message == f"editing {_TEXT_ROW.key}"
    assert after.pending == {}


def _state_with_text_row():
    return State(rows=(_TEXT_ROW, _CHOICE_ROW), cursor=0, pending={}, message="")


def test_keys_pressed_while_editing_type_into_the_buffer_and_never_redirect_the_target():
    state = _state_with_text_row()
    picked = step(state, "e")
    assert picked.editing is not None and picked.buffer == ""
    typed = step(picked, "j")
    assert typed.cursor == picked.cursor and typed.buffer == "j"
    after = apply_text(typed, "new")
    assert after.pending[picked.editing] == "new" and after.editing is None and after.buffer == ""


def test_apply_text_without_e_is_refused():
    state = _state_with_text_row()
    after = apply_text(state, "new")
    assert after.pending == state.pending and "press e" in after.message


def test_apply_text_sets_a_pending_text_value():
    state = State(rows=(_TEXT_ROW,), cursor=0, pending={}, message="")
    after = apply_text(step(state, "e"), "new")
    assert after.pending == {"policy.build_budget_usd_max": "new"}


def test_apply_text_on_a_read_only_row_is_refused_and_does_not_write_pending():
    state = State(rows=(_READONLY_ROW,), cursor=0, pending={}, message="")
    after = apply_text(step(state, "e"), "x")
    assert after.pending == {}


def test_step_u_drops_the_cursor_rows_pending_edit():
    state = State(rows=(_TOGGLE_ROW,), cursor=0, pending={"crew.builder.enabled": True}, message="")
    after = step(state, "u")
    assert after.pending == {}


def test_step_w_and_q_return_a_new_but_equal_state():
    state = State(rows=(_TOGGLE_ROW,), cursor=0, pending={}, message="")
    after_w = step(state, "w")
    after_q = step(state, "q")
    assert after_w == state and after_w is not state
    assert after_q == state and after_q is not state


def test_every_key_on_an_empty_state_is_refused_not_a_crash():
    state = State(rows=(), cursor=0, pending={}, message="")
    keys = ("j", "k", "space", "e", "u", "w", "q")
    assert all(step(state, key).pending == {} for key in keys)
    assert apply_text(state, "x").pending == {}


def test_sections_groups_rows_by_their_leading_key_segment():
    probe = {
        "resolved": {
            "policy": {"review_tier": "2"},
            "crew": {"builder": {"enabled": True, "skills": ["writer"]}},
        },
        "provenance": {
            "policy.review_tier": "base",
            "crew.builder.enabled": "base",
            "crew.builder.skills": "base",
        },
    }
    grouped = sections(rows(probe, team="acme"))
    assert {row.key for row in grouped["crew"]} == {"crew.builder.enabled", "crew.builder.skills"}
    assert {row.key for row in grouped["policy"]} == {"policy.review_tier"}


def test_cycling_a_choice_row_whose_value_is_none_does_not_raise():
    row = Row(key="policy.review_tier", value=None, layer="pat", editable=True, kind="choice", choices=("1", "2", "3"))
    state = State(rows=(row,), cursor=0, pending={}, message="")
    after = step(state, "space")
    assert after.pending["policy.review_tier"] == "1"


def test_step_w_with_pending_yields_write_fragment_then_run_probe_and_keeps_pending():
    state = State(rows=(_TOGGLE_ROW,), cursor=0, pending={"crew.builder.enabled": True}, message="")
    after = step(state, "w")
    assert after.effects == (
        Effect("write_fragment", {"edits": {"crew": {"builder": {"enabled": True}}}}),
        Effect("run_probe", {}),
    )
    assert after.pending == {"crew.builder.enabled": True}


def test_a_keypress_after_w_clears_the_stale_effects_but_keeps_pending():
    state = State(rows=(_TOGGLE_ROW, _READONLY_ROW), cursor=0, pending={"crew.builder.enabled": True}, message="")
    written = step(state, "w")
    after = step(written, "j")
    assert after.effects == ()
    assert after.pending == {"crew.builder.enabled": True}


def test_step_r_returns_a_run_probe_effect():
    state = State(rows=(), cursor=0, pending={}, message="")
    after = step(state, "r")
    assert after.effects == (Effect("run_probe", {}),)


def test_apply_probe_with_an_edited_provenance_keeps_the_row_editable():
    state = State(
        rows=(_TOGGLE_ROW,),
        cursor=0,
        pending={"crew.builder.enabled": True},
        message="",
        team="acme",
    )
    new_probe = {
        "resolved": {"crew": {"builder": {"enabled": True}}},
        "provenance": {"crew.builder.enabled": "edited"},
    }
    after = apply_probe(state, new_probe)
    assert after.pending == {}
    assert after.effects == ()
    row = after.rows[0]
    assert row.layer == "edited"
    assert row.editable is True


def test_apply_probe_rebuilds_rows_using_the_states_own_team_not_the_default():
    state = State(rows=(), cursor=0, pending={}, message="", team="acme")
    probe = {
        "resolved": {"policy": {"review_tier": "2"}},
        "provenance": {"policy.review_tier": "acme"},
    }
    row = apply_probe(state, probe).rows[0]
    assert row.layer == "acme"
    assert row.editable is True


def test_frame_marks_a_pending_row_and_fits_width():
    state = State(rows=(_TOGGLE_ROW, _READONLY_ROW), cursor=0, pending={"crew.builder.enabled": True}, message="ok")
    lines = frame(state, width=60)
    pending_line = next(line for line in lines if "crew.builder.enabled" in line)
    assert pending_line.startswith("*")
    readonly_line = next(line for line in lines if "crew.builder.skills" in line)
    assert "read-only" in readonly_line
    assert lines[-1] == "ok"


def test_frame_shows_the_pending_value_not_the_stale_row_value():
    row = Row(key="crew.builder.enabled", value=False, layer="acme", editable=True, kind="toggle")
    state = State(rows=(row,), cursor=0, pending={"crew.builder.enabled": True}, message="")
    pending_line = next(line for line in frame(state, width=60) if "crew.builder.enabled" in line)
    assert "= True " in pending_line
    assert "= False " not in pending_line


def test_frame_truncates_every_line_to_width():
    state = State(rows=(_TOGGLE_ROW, _READONLY_ROW), cursor=0, pending={"crew.builder.enabled": True}, message="ok")
    lines = frame(state, width=10)
    assert all(len(line) <= 10 for line in lines)


def test_typing_backspace_then_enter_commits_the_edited_text():
    editing = step(State(rows=(_TEXT_ROW,), cursor=0, pending={}, message=""), "e")
    typed = functools.reduce(step, ["a", "b", "BACKSPACE", "c"], editing)
    after = step(typed, "ENTER")
    assert after.pending[_TEXT_ROW.key] == "ac"
    assert after.editing is None
    assert after.buffer == ""


def test_q_while_editing_is_an_ordinary_character_not_a_quit():
    editing = step(State(rows=(_TEXT_ROW,), cursor=0, pending={}, message=""), "e")
    after = step(editing, "q")
    assert after.editing == _TEXT_ROW.key
    assert after.buffer == "q"


def test_esc_cancels_the_edit_and_leaves_the_pending_value_untouched():
    state = State(rows=(_TEXT_ROW,), cursor=0, pending={_TEXT_ROW.key: "old"}, message="")
    editing = step(state, "e")
    typed = step(editing, "x")
    after = step(typed, "ESC")
    assert after.editing is None
    assert after.buffer == ""
    assert after.pending == {_TEXT_ROW.key: "old"}


def test_frame_shows_the_buffer_with_a_trailing_cursor_on_the_row_being_edited():
    editing = step(State(rows=(_TEXT_ROW,), cursor=0, pending={}, message=""), "e")
    typed = step(editing, "x")
    edited_line = next(line for line in frame(typed, width=60) if _TEXT_ROW.key in line)
    assert edited_line.endswith("x_")


def test_apply_effect_result_run_probe_delegates_to_apply_probe():
    state = State(rows=(), cursor=0, pending={"x": 1}, message="", team="acme")
    probe = {
        "resolved": {"policy": {"review_tier": "2"}},
        "provenance": {"policy.review_tier": "acme"},
    }
    after = apply_effect_result(state, Effect("run_probe", {}), probe)
    assert after.pending == {}
    assert after.rows[0].layer == "acme"


def test_apply_effect_result_write_fragment_refusal_keeps_rows_and_pending_and_prefixes_the_section():
    state = State(rows=(_TOGGLE_ROW,), cursor=0, pending={_TOGGLE_ROW.key: True}, message="")
    effect = Effect("write_fragment", {"edits": {"crew": {"builder": {"enabled": True}}}})
    result = {"provenance_error": "base owns this key"}
    after = apply_effect_result(state, effect, result)
    assert after.rows == state.rows
    assert after.pending == state.pending
    assert after.message == "crew: base owns this key"


def test_apply_effect_result_write_fragment_refusal_names_the_section_from_the_effects_own_edits_not_current_pending():
    # `state.pending` has moved on to a different section by the time the
    # refusal comes back; the message must still name the section that was
    # actually written, not whatever is pending now.
    state = State(rows=(_TOGGLE_ROW,), cursor=0, pending={"policy.review_tier": "2"}, message="")
    effect = Effect("write_fragment", {"edits": {"crew": {"builder": {"enabled": True}}}})
    after = apply_effect_result(state, effect, {"provenance_error": "base owns this key"})
    assert after.message == "crew: base owns this key"


def test_apply_effect_result_write_fragment_refusal_names_every_section_when_more_than_one_was_written():
    # the result names no single refused key, so a write spanning two
    # sections must not blame only the alphabetically first one.
    state = State(
        rows=(_TOGGLE_ROW,),
        cursor=0,
        pending={"crew.builder.enabled": True, "policy.review_tier": "2"},
        message="",
    )
    effect = Effect(
        "write_fragment",
        {"edits": {"crew": {"builder": {"enabled": True}}, "policy": {"review_tier": "2"}}},
    )
    after = apply_effect_result(state, effect, {"provenance_error": "refused"})
    assert after.message == "crew, policy: refused"


def test_apply_effect_result_write_fragment_clean_clears_pending_and_queues_run_probe():
    state = State(rows=(_TOGGLE_ROW,), cursor=0, pending={_TOGGLE_ROW.key: True}, message="")
    after = apply_effect_result(state, Effect("write_fragment", {}), {})
    assert after.pending == {}
    assert after.effects == (Effect("run_probe", {}),)


def test_apply_effect_result_init_cartridge_success_carries_the_new_team():
    state = State(rows=(), cursor=0, pending={}, message="", team="")
    after = apply_effect_result(state, Effect("init_cartridge", {"team": "acme"}), {"returncode": 0, "team": "acme"})
    assert after.team == "acme"


def test_apply_effect_result_init_cartridge_failure_is_a_refusal_message():
    state = State(rows=(), cursor=0, pending={}, message="", team="")
    after = apply_effect_result(
        state, Effect("init_cartridge", {"team": "acme"}), {"returncode": 1, "provenance_error": "boom"}
    )
    assert after.team == ""
    assert "boom" in after.message


def test_apply_effect_result_set_profile_team_success_sets_team():
    state = State(rows=(), cursor=0, pending={}, message="", team="")
    after = apply_effect_result(state, Effect("set_profile_team", {}), {"returncode": 0, "team": "acme"})
    assert after.team == "acme"


def test_fold_effects_stops_at_the_first_refusal():
    state = State(rows=(), cursor=0, pending={}, message="", team="")
    results = [
        (Effect("init_cartridge", {"team": "acme"}), {"returncode": 1, "provenance_error": "boom"}),
        (Effect("set_profile_team", {}), {"returncode": 0, "team": "acme"}),
    ]
    after = fold_effects(state, results)
    assert after.team == ""
    assert "boom" in after.message


def test_fold_effects_applies_a_leading_success_before_stopping_at_a_later_refusal():
    state = State(rows=(), cursor=0, pending={}, message="", team="")
    results = [
        (Effect("set_profile_team", {}), {"returncode": 0, "team": "acme"}),
        (Effect("init_cartridge", {"team": "other"}), {"returncode": 1, "provenance_error": "boom"}),
        (Effect("set_profile_team", {}), {"returncode": 0, "team": "third"}),
    ]
    after = fold_effects(state, results)
    assert after.team == "acme"
    assert "boom" in after.message


def test_fold_effects_folds_a_clean_write_with_its_paired_probe_into_fresh_rows_and_no_leftover_effect():
    state = State(rows=(_TOGGLE_ROW,), cursor=0, pending={_TOGGLE_ROW.key: True}, message="", team="acme")
    probe = {
        "resolved": {"crew": {"builder": {"enabled": True}}},
        "provenance": {"crew.builder.enabled": "edited"},
    }
    results = [
        (Effect("write_fragment", {"edits": {"crew": {"builder": {"enabled": True}}}}), {}),
        (Effect("run_probe", {}), probe),
    ]
    after = fold_effects(state, results)
    assert after.pending == {}
    assert after.effects == ()
    assert after.rows[0].layer == "edited"
