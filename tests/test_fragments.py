import pytest

from agent_tools.fragments import (
    FragmentError,
    dump_fragment,
    load_fragment,
    merge_edits,
    round_trips,
)


def test_load_dump_round_trips_a_literal_fragment():
    text = "cast:\n  - reviewer\n  - builder\ncontext:\n  - notes.md\n"
    data = load_fragment(text)
    assert data == {"cast": ["reviewer", "builder"], "context": ["notes.md"]}
    dumped = dump_fragment(data)
    assert load_fragment(dumped) == data


def test_dump_fragment_carries_the_header_comment():
    dumped = dump_fragment({"a": 1})
    lines = dumped.splitlines()
    assert lines[0] == "# written by the cartridge editor; edit freely, it is merged over"
    assert lines[1] == "# cartridge.yaml"


def test_empty_text_loads_to_an_empty_mapping():
    assert load_fragment("") == {}


def test_comment_only_text_loads_to_an_empty_mapping():
    assert load_fragment("# just a comment\n# and another\n") == {}


def test_a_scalar_top_level_is_refused():
    with pytest.raises(FragmentError):
        load_fragment("just a string\n")


def test_a_list_top_level_is_refused():
    with pytest.raises(FragmentError):
        load_fragment("- one\n- two\n")


def test_merge_edits_merges_nested_dicts_with_edits_winning():
    existing = {"cast": {"builder": "codex"}, "team": "pat"}
    edits = {"cast": {"builder": "claude", "reviewer": "codex"}}
    merged = merge_edits(existing, edits)
    assert merged == {
        "cast": {"builder": "claude", "reviewer": "codex"},
        "team": "pat",
    }


def test_merge_edits_replaces_lists_wholesale():
    existing = {"skills": ["a", "b"]}
    edits = {"skills": ["c"]}
    assert merge_edits(existing, edits) == {"skills": ["c"]}


def test_merge_edits_concatenates_context():
    existing = {"context": ["base.md"]}
    edits = {"context": ["extra.md"]}
    assert merge_edits(existing, edits) == {"context": ["base.md", "extra.md"]}


def test_merge_edits_does_not_mutate_its_arguments():
    existing = {"context": ["base.md"], "cast": {"builder": "codex"}}
    edits = {"context": ["extra.md"], "cast": {"builder": "claude"}}
    merge_edits(existing, edits)
    assert existing == {"context": ["base.md"], "cast": {"builder": "codex"}}
    assert edits == {"context": ["extra.md"], "cast": {"builder": "claude"}}


def test_round_trips_true_for_an_ordinary_fragment():
    text = "team: pat\ncast:\n  builder: codex\n"
    assert round_trips(text) is True


def test_round_trips_true_for_an_ordinary_shared_alias():
    # `a` and `b` both alias the same `&common` mapping. safe_load shares one
    # dict object between them; safe_dump re-anchors it on the first
    # occurrence and aliases it on the second. Reloading that output gives
    # back an equal (if no longer identity-shared) structure, so this must
    # not be mistaken for the broken case below: an ordinary alias round
    # -trips cleanly.
    text = "common: &common\n  x: 1\na: *common\nb: *common\n"
    assert round_trips(text) is True


def test_round_trips_false_for_a_self_referential_anchor():
    # `a` anchors itself through its own `self` key. safe_load reconstructs
    # the cycle faithfully, but comparing the reloaded structure against the
    # original with `==` recurses without terminating, so the guard treats
    # this fragment as one that does not round-trip.
    text = "a: &a\n  self: *a\n"
    assert round_trips(text) is False
