from agent_tools.provenance import attribute


def test_a_single_layer_attributes_every_key_to_it():
    layers = [("base", {"policy": {"review_tier": "1"}, "cast": {"builder": "x"}})]
    assert attribute(layers) == {"policy.review_tier": "base", "cast.builder": "base"}


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


def test_cast_and_skills_keys_are_discovered_from_the_final_layer():
    layers = [
        ("base", {"cast": {"builder": "x"}, "skills": {}}),
        ("acme", {"cast": {"builder": "y"}, "skills": {"review": "z"}}),
    ]
    assert attribute(layers) == {"cast.builder": "acme", "skills.review": "acme"}


def test_two_keys_in_the_same_call_are_attributed_to_different_layers():
    layers = [
        ("base", {"policy": {"review_tier": "1"}, "cast": {"builder": "x"}}),
        ("local", {"policy": {"review_tier": "1"}, "cast": {"builder": "y"}}),
        ("acme", {"policy": {"review_tier": "2"}, "cast": {"builder": "y"}}),
    ]
    assert attribute(layers) == {"policy.review_tier": "acme", "cast.builder": "local"}


def test_an_empty_layers_list_yields_an_empty_mapping():
    assert attribute([]) == {}
