from agent_tools.install_exec import _checkout_argv, _fetch_argv

# No umbrella case: the builders hold no umbrella branch, and clone_dir always
# adds the "coxswain-" prefix, so no component resolves to <root>/coxswain.
ROOT = "/r"
RELEASE = {"kind": "fetch_checkout", "component": "cartridges", "tag": "v1", "ref": "main", "channel": "release"}
EDGE = {**RELEASE, "channel": "edge"}


def test_release_fetch_names_prefixed_dir():
    argv = _fetch_argv(RELEASE, ROOT)
    assert argv == ["git", "-C", "/r/coxswain-cartridges", "fetch", "--tags"]
    assert "/r/cartridges" not in argv


def test_release_checkout_names_prefixed_dir():
    argv = _checkout_argv(RELEASE, ROOT)
    assert argv == ["git", "-C", "/r/coxswain-cartridges", "checkout", "v1"]
    assert "/r/cartridges" not in argv


def test_edge_fetch_names_prefixed_dir():
    argv = _fetch_argv(EDGE, ROOT)
    assert argv == ["git", "-C", "/r/coxswain-cartridges", "fetch", "origin",
                    "+refs/heads/main:refs/remotes/origin/main"]
    assert "/r/cartridges" not in argv


def test_edge_checkout_names_prefixed_dir():
    argv = _checkout_argv(EDGE, ROOT)
    assert argv == ["git", "-C", "/r/coxswain-cartridges", "checkout", "-B", "main", "origin/main"]
    assert "/r/cartridges" not in argv
