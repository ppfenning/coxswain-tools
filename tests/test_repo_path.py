import pytest

from agent_tools.repo_path import contract, expand, host_form

HOME = "/".join(["", "home", "alice"])
LINUX_OTHER = "/".join(["", "home", "bob"])
MAC_OTHER = "/".join(["", "Users", "carol"])
REPO = "/".join(["repos", "widget"])


def test_expand_tilde_path_joins_onto_home():
    assert expand("~/" + REPO, HOME) == HOME + "/" + REPO


@pytest.mark.parametrize("other", [LINUX_OTHER, MAC_OTHER])
def test_expand_reroots_another_machines_home(other):
    assert expand(other + "/" + REPO, HOME) == HOME + "/" + REPO


@pytest.mark.parametrize("value", ["/".join(["", "srv", "git", "widget"]), "widget", ""])
def test_expand_leaves_other_values_unchanged(value):
    assert expand(value, HOME) == value


@pytest.mark.parametrize("recorded", ["~/" + REPO, LINUX_OTHER + "/" + REPO, MAC_OTHER + "/" + REPO, HOME + "/" + REPO])
def test_contract_then_expand_round_trips_and_is_idempotent(recorded):
    once = contract(recorded, HOME)
    assert once == "~/" + REPO
    assert contract(once, HOME) == once
    assert expand(once, HOME) == HOME + "/" + REPO


def test_contract_leaves_outside_paths_unchanged():
    outside = "/".join(["", "srv", "git", "widget"])
    assert contract(outside, HOME) == outside
    assert contract("widget", HOME) == "widget"


def test_host_form_of_tilde_and_expanded_forms_agree():
    assert host_form("~/" + REPO, HOME) == REPO
    assert host_form(HOME + "/" + REPO, HOME) == REPO
    assert host_form(MAC_OTHER + "/" + REPO, HOME) == REPO


def test_host_form_leaves_outside_paths_unchanged():
    assert host_form("/".join(["", "srv", "widget"]), HOME) == "/srv/widget"


def test_home_itself_contracts_to_tilde_and_host_maps_to_dot():
    assert contract(HOME, HOME) == "~"
    assert contract(HOME + "/", HOME) == "~"
    assert host_form(HOME, HOME) == "."
    assert expand("~", HOME) == HOME


def test_trailing_slashes_are_dropped():
    assert expand("~/" + REPO + "/", HOME + "/") == HOME + "/" + REPO
    assert contract(HOME + "/" + REPO + "/", HOME) == "~/" + REPO
    assert host_form(HOME + "/" + REPO + "/", HOME) == REPO


def test_tilde_input_drops_trailing_slashes():
    assert contract("~/", HOME) == "~"
    assert host_form("~/", HOME) == "."
    assert contract("~/" + REPO + "/", HOME) == "~/" + REPO
    assert host_form("~/" + REPO + "/", HOME) == host_form(HOME + "/" + REPO + "/", HOME) == REPO


def test_foreign_home_without_repo_part_maps_to_home():
    assert expand(LINUX_OTHER, HOME) == HOME
    assert contract(MAC_OTHER, HOME) == "~"
    assert host_form(MAC_OTHER, HOME) == "."


def test_look_alike_user_is_a_foreign_home_not_a_prefix():
    look_alike = HOME + "2"
    assert contract(look_alike + "/" + REPO, HOME) == "~/" + REPO
    assert expand(look_alike + "/" + REPO, HOME) == HOME + "/" + REPO
