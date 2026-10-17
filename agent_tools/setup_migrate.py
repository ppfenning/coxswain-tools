"""Pure plan for moving an existing local layout to the coxswain home.

The plan follows docs/design/coxswain-home.md, section 4. Nothing here executes a
step: `migrate_plan` takes the observed layout as plain data and returns step dicts
that a later executor applies. Only `observe_install_bins` reads files, and it is an edge.

Observed layout, a dict with these keys:

- `entries`: for each of coxswain-cartridges, coxswain-graphs and coxswain-tools, a dict
  with `old` (the old path), `old_kind` and `new_kind` (each one of absent, dir,
  symlink, file) and `old_target` (where the old path points when it is a symlink).
  The new path is never observed. It is the checkout's name under `Locations.installs`.
- `workspace`: a dict with `path`. The workspace is registered by location and is
  never moved, so the planner only reports where it stays.
- `settings`: profile field to its current value, a string or a list of strings.
- `home_record`: the current text of the home record, or None when it is absent.
- `cartridge_links`: each workspace base and local link to its current lexical target.
- `worktrees`: checkout name to the list of its worktree paths. Required for every
  checkout that needs a repair; a missing one yields a refuse step.
- `worktree_status`: checkout name to "clean" when `git worktree list` names no missing
  path. A checkout at its new path with any other status, or none, is repaired and verified
  again, because the planner cannot tell whether an earlier run finished that step.
- `chair_unit`: the chair systemd unit file text, or None when there is none. A unit whose
  `Environment=PATH=` line still names the old checkout root yields one last `chair_unit` step.

- `install_bins`: optional. Where each editable install points, as `observe_install_bins` returns it.
  `coxswain-graphs` and `coxswain-tools` hold `source`, read from the checkout venv. `tool:coxswain-tools`
  and `tool:coxswain-cartridges` hold `editable` and `extras`, read from the uv tool receipts. A value of
  None is an absent source. When any source differs from its checkout under `Locations.installs`, the plan
  emits eight `run` steps that recreate the venvs and reinstall the tools. When the key is not observed,
  the plan emits none.

- `marketplace`, `marketplace_plugins`, `claude_on_path`: the path registered for coxswain-cartridges
  (None when absent), the plugin ids ending @coxswain-cartridges, and whether `claude` is on PATH. When
  `claude` is on PATH and the registered path is not the checkout under `Locations.installs`, the plan ends
  its body with the `run` steps of `setup_install.marketplace_steps`, then one `claude plugin install` per
  plugin id, or for local-skills@coxswain-cartridges when none was observed. When `claude` is not on PATH
  (or the key is missing) the plan emits one print saying the marketplace was not checked.

Settings may hold tilde-form paths. They are expanded against the user home, taken as the
parent of `Locations.config`, before they are compared with the checkout paths.

Every step is a dict with `op`, a one-sentence `why`, and the fields of its op. The
planner emits no step that deletes or replaces a real directory.
"""

from __future__ import annotations

import json
import posixpath
import tomllib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from agent_tools import repo_path, repo_path_migrate, setup_install
from agent_tools.home import Locations
from agent_tools.repo_path_migrate import Change
from agent_tools.setup_migrate_hosts import ObservedHost, is_migrated

Step = dict[str, Any]

CARTRIDGES = "coxswain-cartridges"
GRAPHS = "coxswain-graphs"
TOOLS = "coxswain-tools"
CHECKOUTS = (CARTRIDGES, GRAPHS, TOOLS)
# uv tool receipt folder for each tool install, keyed by the checkout it installs.
TOOL_RECEIPTS = {TOOLS: "coxswain-tools", CARTRIDGES: "agent-cartridges"}
# Order of the venv steps: tools, graphs, cartridges.
REINSTALL_ORDER = (TOOLS, GRAPHS, CARTRIDGES)
OPS = frozenset(
    {
        "move",
        "symlink",
        "rewrite_setting",
        "rewrite_profile_key",
        "skip",
        "print",
        "refuse",
        "worktree_repair",
        "verify",
        "chair_unit",
        "run",
    }
)
SETTING_KEYS = ("skills_roots", "provider_profile", "harness_dir")
WORKTREES_CLEAN = "no worktree names a missing path"
FINISH_HINT = "run cox setup migrate --finish once every host has migrated."
FINISH_NO_ROOT = "finish: no old root known; pass --old-root"
MARKER_NAME = "migrate.json"


def _step(op: str, why: str, **fields: Any) -> Step:
    return {"op": op, **fields, "why": why}


def _refuse(name: str, reason: str) -> Step:
    return _step("refuse", f"{name} {reason}, so nothing is moved.", checkout=name)


def _checkout(name: str, entry: dict[str, Any] | None, new: str) -> tuple[str, tuple[Step, ...]]:
    """Classify one checkout as moved (steps remain), final (none) or refuse."""
    if not entry or "old" not in entry:
        return "refuse", (_refuse(name, "is missing from the observed entries"),)
    old, old_kind, new_kind = entry["old"], entry.get("old_kind", "absent"), entry.get("new_kind", "absent")
    link = _step(
        "symlink", f"{name} stays reachable at its old path through a link to the new one.", path=old, target=new
    )
    if old_kind == "dir" and new_kind == "absent":
        return "moved", (
            _step("move", f"{name} belongs under the installs folder of the home.", src=old, dst=new),
            link,
        )
    if old_kind == "dir":
        return "refuse", (_refuse(name, f"has a real directory at {old} and something already at {new}"),)
    if old_kind == "absent" and new_kind == "dir":
        return "moved", (link,)
    if old_kind == "absent":
        return "refuse", (_refuse(name, f"is absent at {old} and at {new}"),)
    if old_kind == "symlink" and new_kind == "dir" and entry.get("old_target") == new:
        return "final", ()
    return "refuse", (_refuse(name, f"has {old_kind} at {old} and {new_kind} at {new}, which no step can reconcile"),)


def _repair_steps(name: str, new: str, worktrees: list[str]) -> tuple[Step, Step]:
    return (
        _step(
            "worktree_repair",
            f"The worktree records of {name} still name its old path.",
            checkout=name,
            argv=["git", "-C", new, "worktree", "repair", *worktrees],
        ),
        _step(
            "verify",
            f"{name} must show no worktree at a missing path.",
            checkout=name,
            argv=["git", "-C", new, "worktree", "list", "--porcelain"],
            expect=WORKTREES_CLEAN,
        ),
    )


def _remap(value: Any, pairs: tuple[tuple[str, str], ...]) -> Any:
    """Swap an old checkout prefix for its new one in a string or a list of strings."""
    if isinstance(value, list):
        return [_remap(item, pairs) for item in value]
    if not isinstance(value, str):
        return value
    return next((new + value[len(old) :] for old, new in pairs if value == old or value.startswith(old + "/")), value)


def _expand_all(value: Any, user_home: str) -> Any:
    """Expand a leading tilde in a string or a list of strings."""
    if isinstance(value, list):
        return [_expand_all(item, user_home) for item in value]
    if isinstance(value, str) and (value == "~" or value.startswith("~/")):
        return user_home + value[1:]
    return value


def _setting_steps(
    settings: dict[str, Any], home_record: str | None, pairs: tuple[tuple[str, str], ...], locs: Locations
) -> list[Step]:
    user_home = posixpath.dirname(locs.config)
    keys = [
        _step(
            "rewrite_profile_key",
            f"{key} still points at the old checkout location.",
            key=key,
            old=settings[key],
            new=new,
        )
        for key in SETTING_KEYS
        if key in settings and (new := _remap(expanded := _expand_all(settings[key], user_home), pairs)) != expanded
    ]
    record = (
        []
        if home_record == locs.home
        else [
            _step(
                "rewrite_setting",
                "The home record must name the resolved home.",
                key="home_record",
                path=locs.record,
                old=home_record,
                new=locs.home,
            )
        ]
    )
    return [*keys, *record]


def cartridge_link_target(path: str, locs: Locations) -> str:
    """Expected lexical target of a base or local link: <installs>/coxswain-cartridges/cartridges/<entry name>, relative to the folder holding the link."""
    entry = posixpath.join(locs.installs, CARTRIDGES, "cartridges", posixpath.basename(path))
    return posixpath.relpath(entry, posixpath.dirname(path))


def _link_steps(links: dict[str, str], locs: Locations) -> list[Step]:
    wanted = {path: cartridge_link_target(path, locs) for path in links}
    return [
        _step(
            "symlink",
            "The relative link must reach its cartridges subfolder in the checkout under the home.",
            path=path,
            target=wanted[path],
            old_target=links[path],
        )
        for path in sorted(links)
        if wanted[path] != links[path]
    ]


def _prints(workspace: dict[str, Any] | None, pairs: dict[str, tuple[str, str]], locs: Locations) -> list[Step]:
    moved = {name: {"old": old, "new": new} for name, (old, new) in pairs.items()}
    notes = [
        _step(
            "print",
            FINISH_HINT,
            item="repo paths",
            checkouts=moved,
        ),
    ]
    if not workspace:
        return notes
    path = workspace["path"]
    where = "under the workspaces folder" if path.startswith(locs.workspaces + "/") else "where it is registered"
    return [*notes, _step("print", f"The workspace stays {where} and is never moved.", item="workspace", path=path)]


DEFAULT_PLUGIN = f"local-skills@{CARTRIDGES}"


def _marketplace_steps(observed: dict[str, Any], locs: Locations) -> tuple[list[Step], list[Step]]:
    """Run steps that re-register the marketplace and its plugins, and the one print when claude was not checked."""
    if not observed.get("claude_on_path", False):
        why = "The Claude marketplace was not checked because claude is not on PATH."
        return [], [_step("print", why, item="marketplace not checked")]
    target = posixpath.join(locs.installs, CARTRIDGES)
    registered = observed.get("marketplace")
    if registered == target:
        return [], []
    plugins = observed.get("marketplace_plugins") or [DEFAULT_PLUGIN]
    installs = [
        _step(
            "run",
            f"reinstall {plugin} from the re-registered marketplace",
            argv=["claude", "plugin", "install", plugin],
        )
        for plugin in plugins
    ]
    return [*setup_install.marketplace_steps(target, registered), *installs], []


PATH_PREFIX = "Environment=PATH="


def _chair_unit_steps(unit: str | None, old_root: str) -> list[Step]:
    """One chair_unit step when the unit's PATH line still names `old_root/coxswain-`."""
    lines = [line for line in (unit or "").splitlines() if line.startswith(PATH_PREFIX)]
    if not any(old_root + "/coxswain-" in line for line in lines):
        return []
    why = "The chair systemd unit still names the old checkout root in its PATH, so it is regenerated."
    return [_step("chair_unit", why)]


def _venv_source(checkout: str, dist: str) -> str | None:
    """Edge. The editable source in the dist-info direct_url.json of `checkout`'s .venv, without file://."""
    pattern = f".venv/lib/python*/site-packages/{dist}-*.dist-info/direct_url.json"
    for path in sorted(Path(checkout).glob(pattern)):
        try:
            url = json.loads(path.read_text()).get("url")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(url, str):
            return url.removeprefix("file://")
    return None


def _receipt_entry(tool_dir: str, tool: str) -> dict[str, Any]:
    """Edge. The editable value and extras of `tool`'s uv receipt; None and [] when absent or unreadable."""
    absent: dict[str, Any] = {"editable": None, "extras": []}
    try:
        data = tomllib.loads((Path(tool_dir) / tool / "uv-receipt.toml").read_text())
        requirements = data["tool"]["requirements"]
    except (OSError, ValueError, KeyError, TypeError):
        return absent
    found = [r for r in requirements if isinstance(r, dict) and r.get("name") == tool]
    if not found:
        return absent
    editable, extras = found[0].get("editable"), found[0].get("extras")
    return {
        "editable": editable if isinstance(editable, str) else None,
        "extras": [x for x in extras if isinstance(x, str)] if isinstance(extras, list) else [],
    }


def observe_install_bins(locs: Locations, tool_dir: str) -> dict[str, Any]:
    """Edge. The `install_bins` observation: venv sources of graphs and tools, uv tool receipts of tools and cartridges.

    A missing or unreadable file is recorded as None, never raised. `tool_dir` is the output of `uv tool dir`.
    """
    return {
        GRAPHS: {"source": _venv_source(posixpath.join(locs.installs, GRAPHS), "agent_graphs")},
        TOOLS: {"source": _venv_source(posixpath.join(locs.installs, TOOLS), "coxswain_tools")},
        **{f"tool:{name}": _receipt_entry(tool_dir, folder) for name, folder in TOOL_RECEIPTS.items()},
    }


def _tool_install(name: str, new: str, extras: list[str]) -> Step:
    target = f"{new}[{','.join(extras)}]" if extras else new
    return _step(
        "run",
        f"The {name} uv tool must be editable from its checkout under the installs folder.",
        argv=["uv", "tool", "install", "--force", "-q", "-e", target],
    )


def _reinstall_steps(install_bins: dict[str, Any] | None, news: dict[str, str], locs: Locations) -> list[Step]:
    """Eight run steps when any observed editable source differs from its checkout; none when all match or none was observed."""
    if install_bins is None:
        return []
    tools_receipt = install_bins.get(f"tool:{TOOLS}") or {}
    carts_receipt = install_bins.get(f"tool:{CARTRIDGES}") or {}
    sources = {
        GRAPHS: (install_bins.get(GRAPHS) or {}).get("source"),
        TOOLS: (install_bins.get(TOOLS) or {}).get("source"),
        f"tool:{TOOLS}": tools_receipt.get("editable"),
        f"tool:{CARTRIDGES}": carts_receipt.get("editable"),
    }
    wanted = {GRAPHS: news[GRAPHS], TOOLS: news[TOOLS], f"tool:{TOOLS}": news[TOOLS], f"tool:{CARTRIDGES}": news[CARTRIDGES]}
    if all(sources[key] == wanted[key] for key in sources):
        return []
    venvs = [
        {**step, "why": step["why"] + " because its editable source is not the checkout under the installs folder."}
        for name in REINSTALL_ORDER
        for step in setup_install.venv_steps(name, locs.installs, news[CARTRIDGES], True)
    ]
    return [
        *venvs,
        _tool_install(TOOLS, news[TOOLS], list(tools_receipt.get("extras") or [])),
        _tool_install(CARTRIDGES, news[CARTRIDGES], list(carts_receipt.get("extras") or [])),
    ]


def migrate_plan(observed: dict[str, Any], locs: Locations) -> list[Step]:
    """Ordered steps for the remaining migration; one skip when none remain, only refuses when any checkout refuses."""
    entries = observed.get("entries", {})
    news = {name: posixpath.join(locs.installs, name) for name in CHECKOUTS}
    verdicts = {name: _checkout(name, entries.get(name), news[name]) for name in CHECKOUTS}
    refusals = [step for name in CHECKOUTS if verdicts[name][0] == "refuse" for step in verdicts[name][1]]
    if refusals:
        return refusals
    if "cartridge_links" not in observed:
        why = "The workspace cartridge links were not observed, so their state is unknown and nothing is moved."
        return [_step("refuse", why, checkout="cartridge_links")]
    moved = [name for name in CHECKOUTS if verdicts[name][0] == "moved"]
    worktrees = observed.get("worktrees", {})
    status = observed.get("worktree_status", {})
    repaired = [name for name in CHECKOUTS if name in moved or status.get(name) != "clean"]
    unseen = [
        _refuse(name, "needs its worktrees repaired but their paths were not observed")
        for name in repaired
        if name not in worktrees
    ]
    if unseen:
        return unseen
    user_home = posixpath.dirname(locs.config)
    pairs = {name: (_expand_all(entries[name]["old"], user_home), news[name]) for name in CHECKOUTS}
    market_runs, market_notes = _marketplace_steps(observed, locs)
    body = [
        *(step for name in moved for step in verdicts[name][1]),
        *_link_steps(observed["cartridge_links"], locs),
        *(step for name in repaired for step in _repair_steps(name, news[name], worktrees[name])),
        *_reinstall_steps(observed.get("install_bins"), news, locs),
        *_setting_steps(observed.get("settings", {}), observed.get("home_record"), tuple(pairs.values()), locs),
        *market_runs,
    ]
    # The old root is the parent folder of the old coxswain-cartridges checkout.
    unit = _chair_unit_steps(observed.get("chair_unit"), posixpath.dirname(pairs[CARTRIDGES][0]))
    if not body:
        return unit or [_step("skip", "Every checkout, link, setting and profile key is already final.")]
    return [*body, *_prints(observed.get("workspace"), pairs, locs), *market_notes, *unit]


def _detail(step: Step) -> str:
    op = step["op"]
    if op == "move":
        return f"{step['src']} -> {step['dst']}"
    if op == "symlink":
        return f"{step['path']} -> {step['target']}"
    if op in ("rewrite_setting", "rewrite_profile_key"):
        return f"{step['key']} {step['old']} -> {step['new']}"
    if op in ("worktree_repair", "verify"):
        return " ".join(step["argv"])
    return step.get("checkout") or step.get("item") or ""


def render_plan(steps: list[Step]) -> str:
    """One line per step, numbered from 1, for a dry run."""
    return "\n".join(
        " ".join(part for part in (f"{n}.", step["op"], _detail(step), "-", step["why"]) if part)
        for n, step in enumerate(steps, 1)
    )


class LeaseOutcome(NamedTuple):
    path: str
    granted: bool
    holder: str | None = None
    error: str | None = None


class Lane(NamedTuple):
    name: str
    host: str | None


class ChairLease(NamedTuple):
    released: bool
    stale: bool
    host: str | None


class HostRow(NamedTuple):
    name: str
    state: str


def _lease_line(lease: LeaseOutcome) -> str:
    who = lease.holder or f"an unknown holder (lease error: {lease.error})"
    return f"migrate: refusing, {who} holds {lease.path}"


def preflight_refusals(
    leases: tuple[LeaseOutcome, ...],
    lanes: tuple[Lane, ...],
    hostname: str,
    chair: ChairLease,
    hosts: tuple[HostRow, ...],
) -> list[str]:
    """Refusal lines in the order leases, lanes, chair, host row; a lane with host None counts as local."""
    return [
        *(_lease_line(lease) for lease in leases if not lease.granted),
        *(
            f"migrate: refusing, live lane {lane.name} holds a run on this machine"
            for lane in lanes
            if lane.host is None or lane.host == hostname
        ),
        *(
            [f"migrate: refusing, the chair holds its lease on {hostname}"]
            if not chair.released and not chair.stale and chair.host == hostname
            else []
        ),
        *(
            f"migrate: refusing, host {row.name} holds active state, run cox host drain {row.name} first"
            for row in hosts
            if row.name == hostname and row.state == "active"
        ),
    ]


class LinkFact(NamedTuple):
    """What the edge saw at an old checkout path: kind is absent, dir, symlink or file; target is the raw link text."""

    name: str
    path: str
    kind: str
    target: str | None = None


class FinishFacts(NamedTuple):
    """Everything `finish` decides on. `marker_text` is None when the marker file is absent."""

    old_root: str | None
    marker_text: str | None
    installs: str
    home: str
    plan: Sequence[Step]
    home_rows: Sequence[Mapping[str, Any]]
    hosts: Sequence[ObservedHost]
    rows: Sequence[Mapping[str, Any]]
    files: Mapping[str, str]
    links: Sequence[LinkFact]


class FinishEdge(NamedTuple):
    """The only side effects `finish` may cause, and only in apply mode. `write` takes (old, new) tilde prefixes."""

    write: Callable[[tuple[tuple[str, str], ...]], None]
    rescan: Callable[[], tuple[Sequence[Mapping[str, Any]], Mapping[str, str]]]
    remove: Callable[[str], None]
    delete_marker: Callable[[], None]


class FinishResult(NamedTuple):
    code: int
    lines: tuple[str, ...]
    rewritten: tuple[Change, ...] = ()
    removed: tuple[str, ...] = ()
    kept: tuple[str, ...] = ()


def marker_path(locs: Locations) -> str:
    return posixpath.join(locs.state, MARKER_NAME)


def resolve_old_root(explicit: str | None, marker_text: str | None) -> str | None:
    """The explicit root, else `old_root` from the marker JSON; None when neither names one."""
    if explicit:
        return explicit
    if marker_text is None:
        return None
    try:
        data = json.loads(marker_text)
    except ValueError:
        return None
    root = data.get("old_root") if isinstance(data, dict) else None
    return root if isinstance(root, str) and root else None


def finish_gates(
    plan: Sequence[Step], home_rows: Sequence[Mapping[str, Any]], hosts: Sequence[ObservedHost]
) -> list[str]:
    """One `finish: waiting on` line per failing gate; `hosts` are the active rows only."""
    single_skip = len(plan) == 1 and plan[0].get("op") == "skip"
    return [
        *([] if single_skip else ["finish: waiting on the local plan to reach a single skip step"]),
        *(f"finish: waiting on home check {row.get('check')}" for row in home_rows if not row.get("ok")),
        *(f"finish: waiting on host {h.name} to beat" for h in hosts if h.source != "beat"),
        *(f"finish: waiting on host {h.name} to migrate" for h in hosts if h.source == "beat" and not is_migrated(h)),
    ]


def rewrite_moves(old_root: str, installs: str, home: str) -> tuple[tuple[str, str], ...]:
    """Per checkout, the tilde prefix under the old root and the tilde prefix under the installs folder."""
    return tuple(
        (repo_path.contract(posixpath.join(old_root, n), home), repo_path.contract(posixpath.join(installs, n), home))
        for n in CHECKOUTS
    )


def _under(value: str, prefix: str) -> bool:
    return value == prefix or value.startswith(prefix + "/")


def pending_changes(
    rows: Sequence[Mapping[str, Any]], files: Mapping[str, str], home: str, moves: tuple[tuple[str, str], ...]
) -> list[Change]:
    """Values still under an old prefix. A change that only contracts an unrelated path is not finish's to report."""
    return [
        c
        for old, new in moves
        for c in repo_path_migrate.move_changes(rows, files, home, old, new)
        if _under(repo_path.contract(c["old"], home), old)
    ]


def _verdict(link: LinkFact, installs: str) -> tuple[str, str]:
    want = posixpath.join(installs, link.name)
    if link.kind == "absent":
        return "none", ""
    if link.kind == "symlink" and link.target == want:
        return "remove", ""
    if link.kind == "symlink":
        return "keep", f"a link to {link.target}, not {want}"
    return "keep", "a real directory" if link.kind == "dir" else "a file, not a link"


def removal_decisions(links: Sequence[LinkFact], installs: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Paths to remove: symlinks whose target equals the checkout under `installs`. The rest are kept, with a reason."""
    verdicts = [(link, *_verdict(link, installs)) for link in links]
    return (
        tuple(link.path for link, what, _ in verdicts if what == "remove"),
        tuple(f"{link.path}: {why}" for link, what, why in verdicts if what == "keep"),
    )


def finish(facts: FinishFacts, apply: bool, edge: FinishEdge) -> FinishResult:
    """Plan by default. Gates first, then rewrite and rescan, then link removal, then the marker. Writes only when `apply`."""
    old_root = resolve_old_root(facts.old_root, facts.marker_text)
    if old_root is None:
        return FinishResult(2, (FINISH_NO_ROOT,))
    waiting = finish_gates(facts.plan, facts.home_rows, facts.hosts)
    if waiting:
        return FinishResult(1, tuple(waiting))
    moves = rewrite_moves(old_root, facts.installs, facts.home)
    changes = pending_changes(facts.rows, facts.files, facts.home, moves)
    removals, kept = removal_decisions(facts.links, facts.installs)
    shown = "" if apply else "would "
    kept_lines = tuple(f"finish: kept {k}" for k in kept)
    if not (changes or removals or kept or facts.marker_text is not None):
        return FinishResult(0, ("finish: nothing to do",))
    if apply and changes:
        edge.write(moves)
        left = pending_changes(*edge.rescan(), facts.home, moves)
        if left:
            return FinishResult(1, (f"finish: {len(left)} value(s) still under {old_root}",), tuple(changes))
    if apply:
        for path in removals:
            edge.remove(path)
        if facts.marker_text is not None:
            edge.delete_marker()
    lines = (
        *(f"finish: {shown}rewrite {c['key']}: {c['old']} -> {c['new']}" for c in changes),
        *(f"finish: {shown}remove {path}" for path in removals),
        *kept_lines,
        *([f"finish: {shown}delete the marker"] if facts.marker_text is not None else []),
    )
    return FinishResult(0, lines, tuple(changes), removals, kept)


def observe_links(old_root: str, home: str) -> tuple[LinkFact, ...]:
    """Edge. What sits at <old root>/<name> for each checkout."""
    root = repo_path.expand(old_root, home) if old_root.startswith("~") else old_root
    return tuple(_observe_link(n, posixpath.join(root, n)) for n in CHECKOUTS)


def _observe_link(name: str, path: str) -> LinkFact:
    p = Path(path)
    if p.is_symlink():
        return LinkFact(name, path, "symlink", str(p.readlink()))
    if p.is_dir():
        return LinkFact(name, path, "dir")
    return LinkFact(name, path, "file" if p.exists() else "absent")


def remove_link(path: str) -> None:
    """Edge. Unlinks one symlink; `finish` only passes paths it judged to be links to the installs checkout."""
    Path(path).unlink()


def delete_marker(path: str) -> None:
    """Edge. Removes the marker file; an absent marker is fine."""
    Path(path).unlink(missing_ok=True)
