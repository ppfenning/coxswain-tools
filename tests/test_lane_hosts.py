from agent_tools.lane_hosts import LaneHost, LaneHostError, find_lane_host, parse_lane_hosts

A = {"name": "a", "ssh": "me@a.example", "workspace_dir": "/work/a"}
B = {"name": "b", "ssh": "me@b.example", "workspace_dir": "/work/b"}


def _msg(result) -> str:
    assert isinstance(result, LaneHostError)
    return result.message


def test_an_absent_lane_hosts_key_is_an_empty_tuple():
    assert parse_lane_hosts({}) == ()


def test_two_valid_entries_parse_in_order():
    assert parse_lane_hosts({"lane_hosts": [A, B]}) == (
        LaneHost("a", "me@a.example", "/work/a"),
        LaneHost("b", "me@b.example", "/work/b"),
    )


def test_parsing_leaves_the_input_unchanged():
    profile = {"lane_hosts": [dict(A)]}
    parse_lane_hosts(profile)
    assert profile == {"lane_hosts": [A]}


def test_a_non_list_value_is_an_error():
    assert "must be a list" in _msg(parse_lane_hosts({"lane_hosts": "a"}))


def test_a_non_mapping_entry_is_an_error():
    assert "must be a mapping" in _msg(parse_lane_hosts({"lane_hosts": ["a"]}))


def test_a_missing_name_is_named():
    assert "name" in _msg(parse_lane_hosts({"lane_hosts": [{"ssh": "h", "workspace_dir": "/w"}]}))


def test_a_missing_ssh_is_named():
    assert "ssh" in _msg(parse_lane_hosts({"lane_hosts": [{"name": "a", "workspace_dir": "/w"}]}))


def test_a_missing_workspace_dir_is_named():
    assert "workspace_dir" in _msg(parse_lane_hosts({"lane_hosts": [{"name": "a", "ssh": "h"}]}))


def test_an_empty_ssh_is_an_error():
    assert "ssh" in _msg(parse_lane_hosts({"lane_hosts": [{**A, "ssh": "  "}]}))


def test_a_relative_workspace_dir_is_an_error():
    assert "absolute" in _msg(parse_lane_hosts({"lane_hosts": [{**A, "workspace_dir": "work/a"}]}))


def test_a_duplicate_name_is_named():
    assert "duplicate name: a" in _msg(parse_lane_hosts({"lane_hosts": [A, {**B, "name": "a"}]}))


def test_an_extra_key_is_refused_naming_secrets():
    msg = _msg(parse_lane_hosts({"lane_hosts": [{**A, "identity_file": "~/.ssh/id"}]}))
    assert "identity_file" in msg
    assert "passwords" in msg
    assert "identity files" in msg


def test_find_returns_the_named_host():
    hosts = parse_lane_hosts({"lane_hosts": [A, B]})
    assert find_lane_host(hosts, "b") == LaneHost("b", "me@b.example", "/work/b")


def test_find_returns_none_for_an_unknown_name_and_for_no_hosts():
    hosts = parse_lane_hosts({"lane_hosts": [A]})
    assert find_lane_host(hosts, "z") is None
    assert find_lane_host((), "a") is None
