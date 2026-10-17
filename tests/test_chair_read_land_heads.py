from agent_tools.chair_read_land_heads import read_land_heads

IDS = {("/r", "epic/alpha/p1"): "b1", ("/r", "epic/alpha/p2"): "b2", ("/s", "epic/beta/p1"): "c1"}


def refusal(initiative: str, phase: str) -> dict:
    return {"initiative": initiative, "phase": phase, "run": f"{initiative}-1", "paths": ["a.py"]}


def lookup(repo: str, ref: str) -> str | None:
    return IDS.get((repo, ref))


def test_heads_are_returned_only_for_phases_with_refusals():
    got = read_land_heads([refusal("alpha", "p2")], {"alpha": "/r", "beta": "/s"}, lookup)
    assert got == {"alpha/p2": "b2"}


def test_unknown_repository_yields_nothing_for_that_phase():
    got = read_land_heads([refusal("gamma", "p1"), refusal("beta", "p1")], {"beta": "/s"}, lookup)
    assert got == {"beta/p1": "c1"}


def test_unresolvable_branch_is_omitted():
    got = read_land_heads([refusal("alpha", "p1"), refusal("alpha", "p9")], {"alpha": "/r"}, lookup)
    assert got == {"alpha/p1": "b1"}


def test_a_phase_refused_twice_is_resolved_once():
    calls: list[tuple[str, str]] = []

    def record(repo: str, ref: str) -> str | None:
        calls.append((repo, ref))
        return lookup(repo, ref)

    read_land_heads([refusal("alpha", "p1"), refusal("alpha", "p1")], {"alpha": "/r"}, record)
    assert calls == [("/r", "epic/alpha/p1")]
