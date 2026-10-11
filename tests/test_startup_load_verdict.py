import pytest

from tests.startup_load_verdict import check_clean, check_regression, ratios

CEILING = 10.0
MARGIN = 2.0
STABLE = [(0.8, 0.1), (0.82, 0.1), (0.78, 0.1), (0.81, 0.1), (0.79, 0.1)]


def test_stable_ratio_near_8x_passes():
    verdict = check_clean(STABLE, CEILING)
    assert verdict.ok, str(verdict)
    assert verdict.median_ratio == pytest.approx(8.0)


def test_uniform_slow_host_gives_same_verdict():
    slow = [(cox * 1.5, ref * 1.5) for cox, ref in STABLE]
    verdict = check_clean(slow, CEILING)
    assert verdict.ok, str(verdict)
    assert verdict.median_ratio == pytest.approx(check_clean(STABLE, CEILING).median_ratio)


def test_one_outlier_round_does_not_flip_median():
    verdict = check_clean([*STABLE, (30.0, 0.1)], CEILING)
    assert verdict.ok, str(verdict)


def test_3x_regression_is_detected_against_clean_ratio():
    regressed = [(cox * 3, ref) for cox, ref in STABLE]
    verdict = check_regression(STABLE, regressed, MARGIN)
    assert verdict.ok, str(verdict)


def test_regression_below_margin_is_not_detected():
    regressed = [(cox * 1.5, ref) for cox, ref in STABLE]
    assert not check_regression(STABLE, regressed, MARGIN).ok


def test_empty_input_fails_without_raising():
    assert ratios([]) == ((), None)
    assert not check_clean([], CEILING).ok
    assert not check_regression([], STABLE, MARGIN).ok
    assert not check_regression(STABLE, [], MARGIN).ok
