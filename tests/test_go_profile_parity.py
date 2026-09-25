"""The Python answer for each profile text in CASES is go/testdata/profile/cases.json.
go/profile_test.go checks the same file against ParseProfile, so one file proves parity.
`pytest --regen` rewrites the file from the Python answer before comparing."""

import json
import math
from pathlib import Path

import pytest

from agent_tools import route

CASES_FILE = Path(__file__).resolve().parent.parent / "go" / "testdata" / "profile" / "cases.json"

CASES = [
    "team: t\ncartridges_dir: /c\nskills_roots: [a, b]\nprovider_profile: p\nharness_dir: /h\n"
    "workspace_dir: /w\nassume: a\nforge: github\ntracker: github-projects\n"
    "spend:\n  window_ceiling_usd: 50\n  weekly_ceiling_usd: 907\n",
    "",
    "assume:\n",
    "team: x\n",
    "skills_roots: []\n",
    "skills_roots: [ a , b ]\n",
    "# note\nteam: x\n",
    "team: x   # note\n",
    "team: x#y\n",
    'sources: [{"kind": "tracker", "url": "u"}]\n',
    'repo_map: {"graphs": "g"}\n',
    "repo_map: {bad\n",
    "workspace_dir: C:/w\n",
    "nosuch: 1\n",
    "  team: x\n",
    "spend: 5\n",
    "spend:\n  ceiling: 1\n",
    "spend:\n  window_ceiling_usd: abc\n",
    "teamx\n",
    "spend:\n  window_ceiling_usd: 1_000\n  weekly_ceiling_usd: 1e3\n",
    "spend:\n\tnode_cap_usd: 3\n",
    "spend:\n  window_ceiling_usd: 1\nteam: t\n  node_cap_usd: 2\n",
    "team: x\r\nassume: r\r\n",
    "team: a\rassume: r\r",
    "team: a\vnosuch: 1\n",
    "team: a\fassume: b\x1cforge: c\x1dtracker: d\x1eharness_dir: e\x85router: f\N{LINE SEPARATOR}"
    "provider_profile: g\N{PARAGRAPH SEPARATOR}workspace_dir: h\n",
    "team: x\N{NO-BREAK SPACE}# note\n",
    "team: \x1fx\x1f\n",
    "sources: [1e999]\nspend:\n  window_ceiling_usd: 5\n",
    "sources: [NaN, Infinity, -Infinity]\n",
    'repo_map: {"id": 12345678901234567890, "f": 1.5, "z": -0, "e": 1E2}\n',
    'repo_map: {"s": "a\\u00e9\\ud83d\\ude00\\n", "t": true, "n": null}\n',
    'repo_map: {"a": 1,}\n',
]


def _golden(value):
    """A non-finite float, which JSON cannot hold, is written as {"$float": "inf" | "-inf" | "nan"}."""
    if isinstance(value, float) and not math.isfinite(value):
        return {"$float": repr(value)}
    if isinstance(value, dict):
        return {k: _golden(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_golden(v) for v in value]
    return value


def python_answer(text: str) -> dict:
    """The result or the error message route.parse_profile gives for the profile text."""
    try:
        return {"text": text, "result": _golden(route.parse_profile(text))}
    except route.ProfileError as e:
        return {"text": text, "error": str(e)}


def test_cases_file_matches_python(request: pytest.FixtureRequest) -> None:
    got = json.dumps([python_answer(t) for t in CASES], sort_keys=True, indent=2, allow_nan=False) + "\n"
    if request.config.getoption("--regen"):
        CASES_FILE.parent.mkdir(parents=True, exist_ok=True)
        CASES_FILE.write_text(got)
    assert CASES_FILE.read_text() == got
