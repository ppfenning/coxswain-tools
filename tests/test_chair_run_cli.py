from __future__ import annotations

import contextlib
import dataclasses
import datetime

from conftest import strip_ansi

from agent_tools import chair_run, cli


def test_chair_run_parses_its_three_flags_with_their_defaults() -> None:
    parser = cli.build_parser()
    bare = parser.parse_args(["chair", "run"])
    given = parser.parse_args(["chair", "run", "--once", "--interval", "5", "--dry-run"])
    assert (bare.once, bare.interval, bare.dry_run) == (False, 60.0, False)
    assert (given.once, given.interval, given.dry_run) == (True, 5.0, True)


def test_chair_run_help_names_the_steal_flag_and_the_hard_stop(capsys) -> None:
    with contextlib.suppress(SystemExit):
        cli.build_parser().parse_args(["chair", "run", "--help"])
    out = strip_ansi(capsys.readouterr().out)
    assert "cox route chair take --steal" in out
    assert "weekly_hard_stop_fraction" in out


def test_the_real_bundle_names_every_source_it_cannot_read(tmp_path) -> None:
    deps = cli._chair_run_deps(tmp_path, {}, "chair", 1, "h", False, print)
    assert isinstance(deps, chair_run.RunDeps)
    assert cli._chair_unwired_sources(deps) == [
        "lease", "docket", "approved", "quarantined", "stranded", "attempts",
        "live_initiatives", "intake", "work_store_ready", "sources_configured", "record", "run_id",
    ]


def test_wired_fact_readers_alone_do_not_lift_the_refusal(monkeypatch, tmp_path, capsys) -> None:
    real = cli._chair_run_deps(tmp_path, {}, "chair", 1, "h", False, print)
    readers = {f.name: (lambda: None) for f in dataclasses.fields(real.facts_deps) if callable(getattr(real.facts_deps, f.name))}
    wired = dataclasses.replace(real, facts_deps=dataclasses.replace(real.facts_deps, **readers))
    monkeypatch.setattr(cli, "_leader_runs_dir_or_refuse", lambda _a: ({}, tmp_path, None))
    monkeypatch.setattr(cli, "_chair_run_deps", lambda *_a: wired)
    monkeypatch.setattr(cli.chair_run, "run", lambda *_a: (_ for _ in ()).throw(AssertionError("loop ran")))
    args = cli.build_parser().parse_args(["chair", "run"])
    assert cli._chair_unwired_sources(wired) == ["record", "run_id"]
    assert args.fn(args) == 2
    assert "not wired: record, run_id" in capsys.readouterr().out


def test_chair_run_refuses_before_any_beat_while_a_source_is_unwired(monkeypatch, tmp_path, capsys) -> None:
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(cli, "_leader_runs_dir_or_refuse", lambda _a: ({}, runs_dir, None))
    monkeypatch.setattr(cli.chair, "renew_lease", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("beat ran")))
    monkeypatch.setattr(cli.chair_run, "run", lambda *_a: (_ for _ in ()).throw(AssertionError("loop ran")))
    args = cli.build_parser().parse_args(["chair", "run", "--once"])
    assert args.fn(args) == 2
    assert "refusing to start, no lease taken" in capsys.readouterr().out
    assert not runs_dir.exists()


def test_a_dry_run_beat_touches_no_lease(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cli.chair, "renew_lease", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("beat ran")))
    cli._chair_run_deps(tmp_path, {}, "chair", 1, "h", True, print).beat()
    assert list(tmp_path.iterdir()) == []


def test_once_exits_1_on_a_tick_error_and_releases_a_held_lease(monkeypatch, tmp_path) -> None:
    released: list[str] = []
    monkeypatch.setattr(cli, "_leader_runs_dir_or_refuse", lambda _a: ({}, tmp_path, None))
    monkeypatch.setattr(cli, "_chair_unwired_sources", lambda _f: [])
    monkeypatch.setattr(cli.chair, "_read_lease", lambda *_a: {"epoch": 3})
    monkeypatch.setattr(cli.chair, "release_lease", lambda *_a: released.append("released"))
    monkeypatch.setattr(cli.chair_run, "run", lambda once, interval, dry_run, deps: deps.report_deps.echo("chair 09-26 | tick error: RuntimeError: x"))
    args = cli.build_parser().parse_args(["chair", "run", "--once"])
    assert args.fn(args) == 1
    assert released == ["released"]


def test_once_exit_is_tied_to_the_line_chair_run_prints_for_a_tick_error() -> None:
    now = datetime.datetime(2026, 9, 26, 12, 0, tzinfo=datetime.UTC)
    assert (cli._chair_once_exit(chair_run.error_line(RuntimeError("x"), now)), cli._chair_once_exit("chair 09-26 | holding")) == (1, 0)
