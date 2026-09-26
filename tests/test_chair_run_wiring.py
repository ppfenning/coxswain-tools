from __future__ import annotations

import dataclasses

from agent_tools import cli

TWELVE = {
    "lease", "docket", "approved", "quarantined", "stranded", "attempts",
    "live_initiatives", "intake", "work_store_ready", "sources_configured", "run_id", "record",
}


def test_the_real_deps_hold_no_unwired_source_for_any_of_the_twelve_names(tmp_path) -> None:
    deps = cli._chair_run_deps(tmp_path / "runs", {}, "chair", 1, "h", False, print, tmp_path / "profile.yaml", "files")
    fields = {
        f.name: getattr(bundle, f.name)
        for bundle in (deps.facts_deps, deps.exec_deps)
        for f in dataclasses.fields(bundle)
    }
    assert fields.keys() >= TWELVE
    assert [name for name in TWELVE if isinstance(fields[name], cli._ChairUnwired)] == []
