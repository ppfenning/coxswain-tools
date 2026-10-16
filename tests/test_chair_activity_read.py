from pathlib import Path

from agent_tools.chair_activity_read import prices_for_profile, read_agents, read_session_lines

CATALOG = "models:\n  m1:\n    price: {input: 1.0, output: 2.0}\n"
OTHER = "models:\n  m2:\n    price: {input: 3.0, output: 4.0}\n"


def _tree(tmp_path: Path) -> Path:
    projects = tmp_path / "projects"
    subagents = projects / "proj" / "sess" / "subagents"
    subagents.mkdir(parents=True)
    (projects / "proj" / "sess.jsonl").write_text("s1\ns2\n")
    (subagents / "agent-b.meta.json").write_text('{"kind": "x"}')
    (subagents / "agent-b.jsonl").write_text("b1\nb2\n")
    (subagents / "agent-a.meta.json").write_text('{"kind": "y"}')
    (subagents / "agent-c.meta.json").write_text("not json")
    return projects


def test_read_agents_ids_order_and_skipped_file(tmp_path):
    agents = read_agents(_tree(tmp_path), "sess")
    assert [a["agent_id"] for a in agents] == ["a", "b"]
    assert agents[0] == {"agent_id": "a", "meta": {"kind": "y"}, "lines": []}
    assert agents[1]["lines"] == ["b1", "b2"]


def test_read_agents_empty_or_unknown_session(tmp_path):
    projects = _tree(tmp_path)
    assert read_agents(projects, "") == []
    assert read_agents(projects, "other") == []


def test_read_session_lines(tmp_path):
    projects = _tree(tmp_path)
    assert read_session_lines(projects, "sess") == ["s1", "s2"]
    assert read_session_lines(projects, "other") == []


def test_read_agents_skips_undecodable_unreadable_and_non_object_meta(tmp_path):
    projects = _tree(tmp_path)
    subagents = projects / "proj" / "sess" / "subagents"
    (subagents / "agent-d.meta.json").write_bytes(b"\xff\xfe\x00{")
    (subagents / "agent-e.meta.json").mkdir()
    (subagents / "agent-f.meta.json").write_text("[1, 2]")
    assert [a["agent_id"] for a in read_agents(projects, "sess")] == ["a", "b"]


def test_read_agents_undecodable_transcript_gives_no_lines(tmp_path):
    projects = _tree(tmp_path)
    (projects / "proj" / "sess" / "subagents" / "agent-a.jsonl").write_bytes(b"\xff\xfe")
    assert read_agents(projects, "sess")[0]["lines"] == []


def test_read_session_lines_picks_first_project_in_sorted_order(tmp_path):
    projects = _tree(tmp_path)
    (projects / "aaa").mkdir()
    (projects / "aaa" / "sess.jsonl").write_text("first\n")
    assert read_session_lines(projects, "sess") == ["first"]


def test_read_session_lines_empty_session(tmp_path):
    assert read_session_lines(_tree(tmp_path), "") == []


def _reader(files: dict[Path, str]):
    return files.get


PROFILE = {"provider_profile": "/p/profile.yaml", "cartridges_dir": "/c"}
FIRST = Path("/p/catalog.yaml")
FALLBACK = Path("/c/providers/catalog.yaml")


def test_prices_from_first_catalog():
    read = _reader({FIRST: CATALOG, FALLBACK: OTHER})
    assert prices_for_profile(PROFILE, read) == {"m1": {"input": 1.0, "output": 2.0}}


def test_prices_from_fallback_catalog():
    read = _reader({FALLBACK: OTHER})
    assert prices_for_profile(PROFILE, read) == {"m2": {"input": 3.0, "output": 4.0}}


def test_prices_from_neither_catalog():
    assert prices_for_profile(PROFILE, _reader({})) == {}


def test_prices_from_invalid_yaml():
    assert prices_for_profile(PROFILE, _reader({FIRST: "a: [unclosed"})) == {}


def test_prices_for_empty_profile():
    assert prices_for_profile(None) == {}
    assert prices_for_profile({}) == {}


def test_prices_default_reader_undecodable_catalog_falls_through(tmp_path):
    (tmp_path / "catalog.yaml").write_bytes(b"\xff\xfe\x00")
    cartridges = tmp_path / "cartridges"
    (cartridges / "providers").mkdir(parents=True)
    (cartridges / "providers" / "catalog.yaml").write_text(OTHER)
    profile = {"provider_profile": str(tmp_path / "profile.yaml"), "cartridges_dir": str(cartridges)}
    assert prices_for_profile(profile) == {"m2": {"input": 3.0, "output": 4.0}}
    assert prices_for_profile({"provider_profile": str(tmp_path / "profile.yaml")}) == {}


def test_prices_default_reader_reads_file(tmp_path):
    (tmp_path / "catalog.yaml").write_text(CATALOG)
    profile = {"provider_profile": str(tmp_path / "profile.yaml")}
    assert prices_for_profile(profile) == {"m1": {"input": 1.0, "output": 2.0}}
