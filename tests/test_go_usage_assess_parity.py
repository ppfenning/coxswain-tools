"""The Python answer for each go/testdata/usage-assess case is the case's expected.txt.
go/usage_test.go checks the same file against the Go port, so one file proves parity.
`pytest --regen` rewrites the files from the Python answer before comparing."""

import datetime
from pathlib import Path

import pytest

from agent_tools import cli, pacing, route, usage_window

CASES = Path(__file__).resolve().parent.parent / "go" / "testdata" / "usage-assess"


def _no_ccusage(*_args, **_kwargs):
    """Stands in for the `npx ccusage` call so the run reads the usage files alone, as the Go port does."""
    raise OSError("ccusage is not run in the parity test")


def python_answer(case: Path) -> str:
    """The output line and exit code `cox usage assess` gives for the case at the case's `now`."""
    now = datetime.datetime.fromisoformat((case / "now").read_text().strip())
    runs_dir = case / "runs"
    profile = route.parse_profile((case / "profile.yaml").read_text())
    window = usage_window.gather(runs_dir, now, run=_no_ccusage, ceiling_usd=profile.get("window_ceiling_usd"))
    weekly = usage_window.gather_weekly(runs_dir, now, profile.get("weekly_ceiling_usd"))
    result = pacing.assess(window, cli._resolved_pacing_policy(runs_dir), now, weekly=weekly)
    return f"{result.verdict}: {result.reason}\nexit {cli._USAGE_ASSESS_EXIT[result.verdict]}\n"


@pytest.mark.parametrize("case", sorted(p for p in CASES.iterdir() if p.is_dir()), ids=lambda p: p.name)
def test_python_answer_matches_expected(case: Path, request: pytest.FixtureRequest) -> None:
    got = python_answer(case)
    if request.config.getoption("--regen"):
        (case / "expected.txt").write_text(got)
    assert (case / "expected.txt").read_text() == got


def test_the_cases_reach_every_verdict_the_spike_names() -> None:
    verdicts = {python_answer(p).split(":")[0] for p in CASES.iterdir() if p.is_dir()}
    assert verdicts == {"go", "go_degraded", "hold", "stop"}
