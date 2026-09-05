"""The edge that turns an `install.plan()` step into a real git/cox
invocation. `install.py` already decided what to do; this only turns that
decision into a subprocess call — through an injected `run` — and a record
of what happened. No decision-making lives here: `from_plan` only carries
forward data the planner already knew (a component's `repo`/`tag`, the
requested `team`/`workspace`) so `execute` never has to re-derive it."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

Runner = Callable[[list, str | None], tuple]


def from_plan(steps: Sequence[Mapping], *, manifest: Mapping, options: Mapping) -> list[dict]:
    """Enriches each planner step with the fields `execute` needs to build
    a real command: `repo`/`tag` for `clone`/`fetch_checkout` from the
    manifest, `team`/`workspace` for `setup_install` from the options.
    Leaves `skip`, `refuse`, `doctor` and `desktop` steps as they arrived."""
    components = manifest.get("components", {})
    return [_enrich(step, components, options) for step in steps]


def _enrich(step: Mapping, components: Mapping, options: Mapping) -> dict:
    """One planner step with the fields `execute` needs for its kind."""
    kind = step["kind"]
    if kind in ("clone", "fetch_checkout"):
        spec = components.get(step.get("component"), {})
        return {**step, "repo": spec.get("repo"), "tag": spec.get("tag")}
    if kind == "setup_install":
        return {**step, "team": options.get("team"), "workspace": options.get("workspace")}
    return dict(step)


def _clone_argv(step: Mapping, root: str) -> list:
    return ["git", "clone", "--branch", step["tag"], "--depth", "1",
            f"https://github.com/{step['repo']}.git", f"{root}/{step['component']}"]


def _fetch_argv(step: Mapping, root: str) -> list:
    return ["git", "-C", f"{root}/{step['component']}", "fetch", "--tags"]


def _checkout_argv(step: Mapping, root: str) -> list:
    return ["git", "-C", f"{root}/{step['component']}", "checkout", step["tag"]]


def _setup_install_argv(step: Mapping, root: str) -> list:
    argv = ["cox", "setup", "install", "--root", root]
    if step.get("team") is not None:
        argv += ["--team", step["team"]]
    if step.get("workspace") is not None:
        argv += ["--workspace", step["workspace"]]
    return argv


def _run_step(step: Mapping, root: str, run: Runner) -> tuple:
    """The commands run (always a list of argv, one or more) and the
    (exit, output) actually observed for one runnable step."""
    kind = step["kind"]
    if kind == "clone":
        argv = _clone_argv(step, root)
        exit_code, output = run(argv, None)
        return [argv], exit_code, output
    if kind == "fetch_checkout":
        fetch_argv = _fetch_argv(step, root)
        exit_code, output = run(fetch_argv, None)
        if exit_code != 0:
            return [fetch_argv], exit_code, output
        checkout_argv = _checkout_argv(step, root)
        checkout_exit, checkout_output = run(checkout_argv, None)
        return [fetch_argv, checkout_argv], checkout_exit, output + checkout_output
    if kind == "setup_install":
        argv = _setup_install_argv(step, root)
        exit_code, output = run(argv, None)
        return [argv], exit_code, output
    if kind == "doctor":
        argv = ["cox", "setup", "doctor"]
        exit_code, output = run(argv, None)
        return [argv], exit_code, output
    return [], 1, f"unknown step kind: {kind}"


def execute(steps: Sequence[Mapping], *, root: str, run: Runner) -> list[dict]:
    """Runs `steps` in order through the injected `run`, stopping at the
    first non-zero exit or at a `refuse`. `skip` runs nothing and reports
    clean; `desktop` prints its no-op line instead of calling `run`. Every
    step gets one `{"step", "commands", "exit", "output"}` record (`commands` is
    always a list of argv, empty for a step that ran no command); steps after
    a stop carry `exit: None` because they never ran."""
    results = []
    stopped = False
    for step in steps:
        kind = step["kind"]
        if stopped:
            results.append({"step": step, "commands": [], "exit": None, "output": None})
            continue
        if kind == "skip":
            results.append({"step": step, "commands": [], "exit": 0, "output": ""})
            continue
        if kind == "refuse":
            results.append({"step": step, "commands": [], "exit": 2, "output": step.get("detail", "")})
            stopped = True
            continue
        if kind == "desktop":
            output = "desktop: planned"
            print(output)
            results.append({"step": step, "commands": [], "exit": 0, "output": output})
            continue
        commands, exit_code, output = _run_step(step, root, run)
        results.append({"step": step, "commands": commands, "exit": exit_code, "output": output})
        if exit_code != 0:
            stopped = True
    return results
