from types import SimpleNamespace

from agent_tools import forge, forge_github, route


def test_forge_name_defaults_to_local():
    assert forge.forge_name({}) == "local"


def test_forge_name_reads_the_profiles_forge_key():
    assert forge.forge_name({"forge": "github"}) == "github"


def test_forge_for_returns_the_builtin_module():
    assert forge.forge_for("github") is forge_github


def test_forge_for_is_none_for_an_unknown_name():
    assert forge.forge_for("no-such-forge") is None


def test_forge_for_finds_an_entry_point_forge(monkeypatch):
    module = SimpleNamespace(name="acme forge")
    registered = [SimpleNamespace(name="acme", load=lambda: module)]
    monkeypatch.setattr(forge.importlib.metadata, "entry_points", lambda group: registered if group == "coxswain.forges" else [])
    assert forge.forge_for("acme") is module
    assert forge.forge_for("other") is None


def test_parse_profile_accepts_a_forge_line():
    assert route.parse_profile("forge: github\n")["forge"] == "github"
