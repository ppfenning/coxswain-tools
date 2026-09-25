"""The Python answer for each argv in CASES is recorded in go/testdata/parse/cases.json.
go/parse_test.go checks the same file against the Go parser, so one file proves parity.
`pytest --regen` rewrites the file from the Python answer before comparing."""

import argparse
import json
from pathlib import Path

import pytest

from agent_tools import cli

CASES_FILE = Path(__file__).resolve().parent.parent / "go" / "testdata" / "parse" / "cases.json"

CASES = [
    ["runs", "usage", "r-1"],
    ["runs", "usage", "--json", "r-1", "--runs-dir", "x"],
    ["runs", "usage", "r-1", "--runs-dir=x"],
    ["runs", "usage", "r-1", "--runs", "x"],
    ["runs", "usage"],
    ["runs", "usage", "r-1", "--nope"],
    ["runs", "usage", "r-1", "extra"],
    ["runs", "land", "r-1", "--repo", "R", "--gate", "full"],
    ["runs", "land", "r-1", "--repo", "R", "--gate", "nope"],
    ["runs", "land", "r-1"],
    ["runs", "top", "--interval", "0.5"],
    ["runs", "top", "--interval", "x"],
    ["stats", "ingest"],
    ["stats", "ingest", "other"],
    ["stats", "chair", "--session", "a", "--session", "b"],
    ["install", "--root", "R", "--with", "crew", "--with", "x"],
    ["setup", "install", "--root", "R", "--team", "T", "--workspace", "W", "--weekly-ceiling-usd", "900"],
    ["setup", "install", "--root", "R", "--team", "T", "--workspace", "W", "--w", "1"],
    ["route", "chair", "take", "--pid", "12"],
    ["route", "chair", "take", "--pid", "x"],
    ["runs"],
    ["versions"],
    ["nosuch"],
]


class ParseFail(Exception):
    def __init__(self, prog: str, message: str) -> None:
        super().__init__(prog, message)
        self.prog = prog
        self.message = message


def _raise(self: argparse.ArgumentParser, message: str) -> None:
    raise ParseFail(self.prog, message)


def python_answer(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> dict:
    """What `cox <argv>` parses to, or the parser and message that rejected it."""
    monkeypatch.setattr(argparse.ArgumentParser, "error", _raise)
    parser = cli.build_parser()
    try:
        ns = parser.parse_args(argv)
    except ParseFail as e:
        return {"argv": argv, "error": {"prog": e.prog, "message": e.message}}
    own = {a.dest for a in parser._actions if not isinstance(a, argparse._SubParsersAction)}
    values = {k: v for k, v in vars(ns).items() if k != "fn" and k not in own}
    return {"argv": argv, "values": values}


def test_python_answers_match_the_cases_file(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    got = [python_answer(argv, monkeypatch) for argv in CASES]
    if request.config.getoption("--regen"):
        CASES_FILE.parent.mkdir(parents=True, exist_ok=True)
        CASES_FILE.write_text(json.dumps(got, sort_keys=True, indent=2) + "\n")
    assert json.loads(CASES_FILE.read_text()) == got
