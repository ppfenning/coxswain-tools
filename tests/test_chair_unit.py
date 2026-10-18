from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from agent_tools.chair_unit import PathObservation, effective_path, read_chair_path

UNIT = "[Service]\nEnvironment=PATH=/usr/bin\nExecStart=/bin/true\n"


def test_unit_only() -> None:
    assert effective_path(UNIT, ()) == PathObservation("/usr/bin", "coxswain-chair.service")


def test_drop_in_only() -> None:
    got = effective_path(None, (("10-path.conf", "[Service]\nEnvironment=PATH=/opt/bin\n"),))
    assert got == PathObservation("/opt/bin", "10-path.conf")


def test_drop_in_wins_over_unit() -> None:
    got = effective_path(UNIT, (("10-path.conf", "Environment=PATH=/opt/bin\n"),))
    assert got == PathObservation("/opt/bin", "10-path.conf")


def test_later_drop_in_name_wins_regardless_of_order() -> None:
    drop_ins = (
        ("20-b.conf", "Environment=PATH=/b\n"),
        ("10-a.conf", "Environment=PATH=/a\n"),
    )
    assert effective_path(UNIT, drop_ins) == PathObservation("/b", "20-b.conf")
    assert drop_ins[0][0] == "20-b.conf"


def test_drop_without_path_leaves_unit_value() -> None:
    got = effective_path(UNIT, (("10-x.conf", "Environment=FOO=bar\nEnvironment=PATHX=/z\n"),))
    assert got == PathObservation("/usr/bin", "coxswain-chair.service")


def test_neither_returns_none() -> None:
    assert effective_path(None, ()) is None
    assert effective_path("[Service]\nEnvironment=FOO=bar\n", (("a.conf", ""),)) is None


def test_quoted_form() -> None:
    got = effective_path('Environment="PATH=/q/bin:/usr/bin"\n', ())
    assert got == PathObservation("/q/bin:/usr/bin", "coxswain-chair.service")


def test_last_assignment_in_a_file_wins() -> None:
    text = "Environment=PATH=/first\nEnvironment=FOO=x\nEnvironment=PATH=/second\n"
    assert effective_path(text, ()) == PathObservation("/second", "coxswain-chair.service")


def test_observation_is_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        PathObservation("/a", "b").path = "/c"  # type: ignore[misc]


def test_read_chair_path_over_directory(tmp_path: Path) -> None:
    (tmp_path / "coxswain-chair.service").write_text(UNIT, encoding="utf-8")
    assert read_chair_path(tmp_path) == PathObservation("/usr/bin", "coxswain-chair.service")
    drop_dir = tmp_path / "coxswain-chair.service.d"
    drop_dir.mkdir()
    (drop_dir / "10-path.conf").write_text("Environment=PATH=/opt/bin\n", encoding="utf-8")
    (drop_dir / "notes.txt").write_text("Environment=PATH=/ignored\n", encoding="utf-8")
    assert read_chair_path(tmp_path) == PathObservation("/opt/bin", "10-path.conf")
