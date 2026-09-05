from agent_tools.editor_model import rows, sections
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
