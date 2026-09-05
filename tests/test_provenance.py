from agent_tools.provenance import attribute


def test_a_single_layer_attributes_every_key_to_it():
    layers = [("base", {"policy": {"review_tier": "1"}, "cast": {"builder": "x"}})]
    assert attribute(layers) == {"policy.review_tier": "base", "crew.builder": "base"}


def test_a_dict_shaped_seat_splits_into_enabled_and_skills_fields():
    layers = [
        (
            "base",
            {
                "policy": {"review_tier": "1"},
                "crew": {"builder": {"enabled": True, "skills": ["a"]}},
            },
        )
    ]
    assert attribute(layers) == {
        "policy.review_tier": "base",
        "crew.builder.enabled": "base",
        "crew.builder.skills": "base",
    }


def test_three_layers_where_the_last_one_changes_the_key():
    layers = [
        ("base", {"policy": {"review_tier": "1"}}),
        ("local", {"policy": {"review_tier": "1"}}),
        ("acme", {"policy": {"review_tier": "2"}}),
    ]
    assert attribute(layers) == {"policy.review_tier": "acme"}


def test_a_fragment_label_ending_in_edited_yaml_is_reported_as_edited():
    layers = [
        ("base", {"policy": {"review_tier": "1"}}),
        ("acme/cartridge.d/edited.yaml", {"policy": {"review_tier": "3"}}),
    ]
    assert attribute(layers) == {"policy.review_tier": "edited"}


def test_a_key_never_set_in_any_layer_is_absent_from_the_result():
    layers = [("base", {"policy": {"review_tier": "1"}})]
    result = attribute(layers)
    assert "policy.build_budget_usd_max" not in result
    assert "policy.review_tier" in result


def test_an_unchanged_later_layer_keeps_the_earlier_labels_attribution():
    layers = [
        ("base", {"policy": {"review_tier": "1"}}),
        ("local", {"policy": {"review_tier": "1"}}),
        ("acme", {"policy": {"review_tier": "1"}}),
    ]
    assert attribute(layers) == {"policy.review_tier": "base"}


def test_crew_and_skills_keys_are_discovered_from_the_final_layer():
    layers = [
        ("base", {"crew": {"builder": {"enabled": True, "skills": ["x"]}}, "skills": {}}),
        ("acme", {"crew": {"builder": {"enabled": True, "skills": ["y"]}}, "skills": {"review": "z"}}),
    ]
    assert attribute(layers) == {
        "crew.builder.enabled": "base",
        "crew.builder.skills": "acme",
        "skills.review": "acme",
    }


def test_two_keys_in_the_same_call_are_attributed_to_different_layers():
    layers = [
        ("base", {"policy": {"review_tier": "1"}, "crew": {"builder": {"enabled": True}}}),
        ("local", {"policy": {"review_tier": "1"}, "crew": {"builder": {"enabled": False}}}),
        ("acme", {"policy": {"review_tier": "2"}, "crew": {"builder": {"enabled": False}}}),
    ]
    assert attribute(layers) == {"policy.review_tier": "acme", "crew.builder.enabled": "local"}


def test_a_seat_present_only_under_the_mirrored_cast_key_is_still_read_as_crew():
    layers = [
        ("base", {"cast": {"builder": {"enabled": True, "skills": ["a"]}}}),
        ("acme", {"cast": {"builder": {"enabled": True, "skills": ["b"]}}}),
    ]
    assert attribute(layers) == {
        "crew.builder.enabled": "base",
        "crew.builder.skills": "acme",
    }


def test_a_seat_with_enabled_from_base_and_skills_from_the_team_gets_two_labels():
    layers = [
        ("base", {"crew": {"builder": {"enabled": True, "skills": ["a"]}}}),
        ("acme", {"crew": {"builder": {"enabled": True, "skills": ["b"]}}}),
    ]
    assert attribute(layers) == {
        "crew.builder.enabled": "base",
        "crew.builder.skills": "acme",
    }


def test_a_scalar_seat_value_is_attributed_as_a_whole_key_not_split_into_fields():
    layers = [
        ("base", {"cast": {"builder": "x"}}),
        ("acme", {"cast": {"builder": "y"}}),
    ]
    assert attribute(layers) == {"crew.builder": "acme"}


def test_a_seat_field_no_layer_sets_is_absent_not_attributed_to_base():
    layers = [("base", {"crew": {"builder": {"enabled": True}}})]
    result = attribute(layers)
    assert result == {"crew.builder.enabled": "base"}
    assert "crew.builder.skills" not in result


def test_an_empty_layers_list_yields_an_empty_mapping():
    assert attribute([]) == {}
