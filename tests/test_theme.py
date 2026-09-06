from agent_tools import theme


def test_every_theme_defines_every_role():
    for t in (theme.default, theme.gruvbox, theme.nord):
        assert set(t) == set(theme.ROLES)


def test_resolve_falls_back_to_default_for_an_unknown_name():
    assert theme.resolve("not-a-theme") == dict(theme.default)
    assert theme.resolve("nord") == dict(theme.nord)


def test_pair_numbers_are_stable_across_two_calls():
    assert theme.pair_numbers(theme.default) == theme.pair_numbers(theme.default)


def test_pair_numbers_are_distinct_per_role():
    numbers = theme.pair_numbers(theme.gruvbox)
    assert len(set(numbers.values())) == len(numbers)
