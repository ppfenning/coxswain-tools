from agent_tools.versions_hint import same_release, status_label, umbrella_hint, version_key


def test_hint_names_both_tags_when_umbrella_differs():
    hint = umbrella_hint("v0.9.0", "v1.0.0")
    assert hint is not None
    assert "v0.9.0" in hint and "v1.0.0" in hint
    assert "umbrella checkout" in hint
    assert "\n" not in hint


def test_hint_absent_when_tags_equal():
    assert umbrella_hint("v1.0.0", "v1.0.0") is None


def test_hint_absent_when_either_tag_is_none():
    assert umbrella_hint(None, "v1.0.0") is None
    assert umbrella_hint("v1.0.0", None) is None
    assert umbrella_hint(None, None) is None


def test_hint_absent_for_same_release_in_different_formats():
    assert umbrella_hint("v0.26.0", "0.26.0") is None
    assert umbrella_hint("v0.37.0", "v0.37.0-19-gaa15d67") is None
    assert umbrella_hint("v1.0", "v1.0.0") is None


def test_hint_present_for_older_umbrella_in_mixed_formats():
    assert umbrella_hint("v0.25.0", "0.26.0") is not None
    assert umbrella_hint("v0.9.0", "v0.10.0-2-gabc") is not None


def test_hint_absent_when_umbrella_is_newer_or_unparseable():
    assert umbrella_hint("v1.1.0", "v1.0.0") is None
    assert umbrella_hint("nightly", "v1.0.0") is None


def test_version_key_ignores_prefix_suffix_and_trailing_zeros():
    assert version_key("v0.26.0") == (0, 26)
    assert version_key("0.26.0") == (0, 26)
    assert version_key("v0.37.0-19-gaa15d67") == (0, 37)
    assert version_key("nightly") is None


def test_same_release_falls_back_to_string_equality():
    assert same_release("v0.26.0", "0.26.0")
    assert not same_release("v0.26.0", "v0.27.0")
    assert same_release("nightly", "nightly")
    assert not same_release("nightly", "v1.0.0")


def test_untagged_when_drift_and_release_not_offered():
    label = status_label("drift", "v0.9.0", "v1.0.0", frozenset({"v0.9.0"}))
    assert label == "untagged"
    assert label != "drift"
    assert label != "ok"


def test_status_unchanged_otherwise():
    assert status_label("ok", "v1.0.0", "v1.0.0", frozenset()) == "ok"
    assert status_label("ok", "v0.9.0", "v1.0.0", frozenset()) == "ok"
    assert status_label("drift", "v0.9.0", "v1.0.0", frozenset({"v1.0.0"})) == "drift"
    assert status_label("drift", "v1.0.0", "v1.0.0", frozenset()) == "drift"


def test_status_compares_across_tag_formats():
    assert status_label("drift", "v0.25.0", "0.26.0", frozenset({"v0.26.0"})) == "drift"
    assert status_label("drift", "v0.25.0", "v0.26.0-3-gabc", frozenset({"v0.26.0"})) == "drift"
    assert status_label("drift", "v0.26.0", "0.26.0", frozenset()) == "drift"
    assert status_label("drift", "v0.25.0", "0.26.0", frozenset({"v0.25.0"})) == "untagged"
