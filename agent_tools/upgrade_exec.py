"""Runs an upgrade Plan: git fetch and checkout per step, crew's install script after crew moves.
The only side effects are the injected `run(argv, cwd) -> (exit, output)` and print."""
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from agent_tools.upgrade_plan import Plan, Step

Run = Callable[[list, str | None], tuple]


def offered_tags(ls_remote_output: str) -> tuple[str, ...]:
    """Tag names in `git ls-remote --tags` output, in the order given, with peeled `^{}` duplicates dropped."""
    names = (
        line.split("\t", 1)[1].removeprefix("refs/tags/").removesuffix("^{}")
        for line in ls_remote_output.splitlines()
        if "\trefs/tags/" in line
    )
    return tuple(dict.fromkeys(names))


def newest_release(tags: Sequence[str]) -> str | None:
    """The first tag shaped `v1.2.3`; a prerelease such as `v1.2.3-rc1` is never a default target."""
    return next((tag for tag in tags if re.fullmatch(r"v\d+(\.\d+)*", tag)), None)


def _step_dir(root: Path, step: Step) -> Path:
    """The umbrella checkout is `<root>/coxswain`, not a manifest component."""
    return root / "coxswain" if step.kind == "umbrella" else root / step.name


def _move(step: Step, root: Path, run: Run, clone_url: str | None) -> tuple:
    """A component with a clone URL has no checkout yet: clone it instead of fetching."""
    directory = str(_step_dir(root, step))
    first = ["git", "clone", clone_url, directory] if clone_url else ["git", "-C", directory, "fetch", "--tags"]
    for argv in (first, ["git", "-C", directory, "checkout", step.to_tag]):
        code, output = run(argv, None)
        if code != 0:
            return code, output
    return 0, ""


def _describe(step: Step) -> str:
    return f"{step.name}: {step.from_tag or 'not installed'} to {step.to_tag}"


def run_plan(
    plan: Plan, *, root: Path | str, run: Run, dry_run: bool, clone_urls: Mapping[str, str] | None = None
) -> int:
    """Exit 2 on a failed command or when any component is left behind, else 0. A dry run makes no `run` call.
    `clone_urls` names the components with no checkout yet."""
    root = Path(root)
    urls = clone_urls or {}
    install_script = root / "crew" / "install.sh"
    for step in plan.steps:
        print(f"would move {_describe(step)}" if dry_run else f"moving {_describe(step)}")
        moves_crew = step.kind == "component" and step.name == "crew"
        if dry_run:
            if moves_crew:
                print(f"would run {install_script}")
            continue
        code, output = _move(step, root, run, urls.get(step.name) if step.kind == "component" else None)
        if code == 0 and moves_crew:
            code, output = run([str(install_script)], str(install_script.parent))
        if code != 0:
            print(f"FAILED: {step.name}: {output.strip()}")
            return 2
    for item in plan.left_behind:
        print(f"left behind: {item.name}: {item.reason}: {item.detail}")
    return 2 if plan.left_behind else 0
