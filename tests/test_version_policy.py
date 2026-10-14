from agent_tools.version_policy import Version, canonical, parse, tag

V039 = Version(0, 39, 0)


def test_three_spellings_parse_to_one_version():
    assert parse("0.39.0") == parse("v0.39.0") == V039


def test_short_forms_parse():
    assert parse("v39") == V039
    assert parse("0.39") == V039


def test_bare_lone_number_is_unreadable():
    assert parse("39") is None


def test_garbage_returns_none():
    assert parse("not-a-version") is None
    assert parse("") is None


def test_canonical_and_tag_round_trip():
    assert canonical(V039) == "0.39.0"
    assert tag(V039) == "v0.39.0"
    assert parse(canonical(V039)) == V039
    assert parse(tag(V039)) == V039


def test_prerelease_parses_and_round_trips():
    assert parse("v1.0.0-rc1") == Version(1, 0, 0, "rc1")
    assert canonical(Version(1, 0, 0, "rc1")) == "1.0.0-rc1"


def test_rc_sorts_below_release_and_above_prior():
    assert parse("0.40.0") < parse("v1.0.0-rc1") < parse("1.0.0")
