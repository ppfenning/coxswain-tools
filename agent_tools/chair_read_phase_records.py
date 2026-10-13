"""The `phase_records` source of a chair tick: which approved tasks' `<run>:<phase>` have a phase record.

`cox runs land --phase` refuses a run with no phase record ("land: no phase record at ..."), so the land planner
lands such a phase by task instead. `phase_keys` and `recorded_keys` are pure; `read_phase_records` is the edge,
and it looks a record up the way the land command does (`cli._land_phase_record`): a manifest in the run store or
a manifest file, else the file `runs/<run>:<phase>.json`.
"""
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path

from agent_tools import run_store
from agent_tools.chair_types import ApprovedTask


def phase_keys(approved: Sequence[ApprovedTask]) -> list[str]:
    """The sorted, distinct `<run>:<phase>` of the approved tasks; a task whose run is unknown names none."""
    return sorted({f"{t['run']}:{t['phase']}" for t in approved if t["run"]})


def recorded_keys(keys: Sequence[str], manifests: Mapping[str, Sequence[Mapping]], file_exists: Callable[[str], bool]) -> list[str]:
    """The keys with a phase record: a manifest of the key's run whose `run_id` is the key, else a file for the key."""
    named: dict[str, Collection[object]] = {run: {m.get("run_id") for m in ms} for run, ms in manifests.items()}
    return [k for k in keys if k in named.get(k.rpartition(":")[0], ()) or file_exists(k)]


def read_phase_records(runs_dir: Path, approved: Sequence[ApprovedTask]) -> list[str]:
    """Edge. `<run>:<phase>` of each approved task's phase that has a phase record, every run's manifests read once."""
    keys = phase_keys(approved)
    if not keys:
        return []
    root = Path(runs_dir)
    return recorded_keys(keys, run_store.all_phase_manifests(root), lambda key: (root / f"{key}.json").exists())
