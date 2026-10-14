import dataclasses
from datetime import date

from agent_tools.deprecations import Deprecation, DeprecationError, Evaluation, evaluate, make_deprecation

DAY = date(2026, 10, 14)
BY_DATE = Deprecation("old-flag", "use --new instead", "cli", on_date=DAY)
BY_CHECK = Deprecation("old-store", "use the lake", "store", check="lake_ready")


def test_date_before_is_not_met():
    assert evaluate((BY_DATE,), date(2026, 10, 13), {}) == Evaluation(met=(), unknown=())


def test_date_on_is_met():
    assert evaluate((BY_DATE,), DAY, {}) == Evaluation(met=(BY_DATE,), unknown=())


def test_date_after_is_met():
    assert evaluate((BY_DATE,), date(2026, 10, 15), {}) == Evaluation(met=(BY_DATE,), unknown=())


def test_check_true_is_met():
    assert evaluate((BY_CHECK,), DAY, {"lake_ready": True}) == Evaluation(met=(BY_CHECK,), unknown=())


def test_check_false_is_neither_met_nor_unknown():
    assert evaluate((BY_CHECK,), DAY, {"lake_ready": False}) == Evaluation(met=(), unknown=())


def test_check_missing_is_unknown_and_not_met():
    assert evaluate((BY_CHECK,), DAY, {}) == Evaluation(met=(), unknown=(BY_CHECK,))


def test_both_date_and_check_is_refused():
    assert isinstance(make_deprecation("x", "m", "o", on_date=DAY, check="c"), DeprecationError)


def test_neither_date_nor_check_is_refused():
    assert isinstance(make_deprecation("x", "m", "o"), DeprecationError)


def test_valid_construction_returns_a_frozen_deprecation():
    dep = make_deprecation("x", "m", "o", check="c")
    assert dep == Deprecation("x", "m", "o", None, "c")
    try:
        dep.name = "y"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("Deprecation is not frozen")
