"""Pure verdict core for the interleaved startup load check: timing data in, verdict out, no I/O."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median

Round = tuple[float, float]


@dataclass(frozen=True)
class CleanVerdict:
    ok: bool
    median_ratio: float | None
    ceiling: float
    rounds: int

    def __str__(self) -> str:
        return f"clean median ratio {self.median_ratio} vs ceiling {self.ceiling} over {self.rounds} rounds"


@dataclass(frozen=True)
class RegressionVerdict:
    ok: bool
    clean_median: float | None
    regressed_median: float | None
    margin: float

    def __str__(self) -> str:
        return f"regressed median {self.regressed_median} vs clean median {self.clean_median} x margin {self.margin}"


def ratios(rounds: Sequence[Round]) -> tuple[tuple[float, ...], float | None]:
    """Per-round cox/reference ratios and their median; rounds with a non-positive reference are skipped."""
    per_round = tuple(cox / ref for cox, ref in rounds if ref > 0)
    return per_round, (median(per_round) if per_round else None)


def check_clean(rounds: Sequence[Round], ceiling: float) -> CleanVerdict:
    per_round, mid = ratios(rounds)
    return CleanVerdict(mid is not None and mid <= ceiling, mid, ceiling, len(per_round))


def check_regression(clean: Sequence[Round], regressed: Sequence[Round], margin: float) -> RegressionVerdict:
    """Pass only when the regressed median ratio exceeds the clean median ratio times margin."""
    _, clean_mid = ratios(clean)
    _, regressed_mid = ratios(regressed)
    detected = clean_mid is not None and regressed_mid is not None and regressed_mid > clean_mid * margin
    return RegressionVerdict(detected, clean_mid, regressed_mid, margin)
