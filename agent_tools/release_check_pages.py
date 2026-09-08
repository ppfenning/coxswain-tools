"""The package-pages check: each component's pyproject, README and built PKG-INFO against the publish rules."""

from __future__ import annotations

import re
import tarfile
import tempfile
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from agent_tools import release

if TYPE_CHECKING:
    from agent_tools.release_check import Drift

ALIASES = ("agent-tools", "cast")
DEPRECATION_MARKERS = ("deprecated", "deprecation", "no longer", "retired", "will be removed")
_SENTENCE_RE = re.compile(r"[^.]*\.")


def readme_h1(text: str) -> str | None:
    return next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), None)


def alias_sentences(text: str, aliases: tuple[str, ...]) -> list[tuple[int, str]]:
    return [
        (text[: m.start()].count("\n") + 1, m.group().strip())
        for m in _SENTENCE_RE.finditer(text)
        if any(alias in m.group() for alias in aliases)
    ]


def description_ok(text: str) -> bool:
    return bool(text) and len(text) < 160 and text.count(".") <= 1


def check_pages(facts: Mapping) -> list[Drift]:
    from agent_tools.release_check import Drift

    def package_drifts(pkg: Mapping) -> list[Drift]:
        name = pkg["name"]
        pyproject = pkg.get("pyproject") or {}
        readme = pkg.get("readme") or ""
        pkg_info = pkg.get("pkg_info")
        pyproject_file = f"{name}/pyproject.toml"
        readme_file = f"{name}/README.md"
        project = pyproject.get("project", {})
        description = project.get("description", "")
        drifts: list[Drift] = []
        if "readme" not in project:
            drifts.append(Drift("package_pages", pyproject_file, None, pyproject_file, None, f"add readme to [project] in {pyproject_file}"))
        body = pkg_info.split("\n\n", 1)[1].strip() if pkg_info and "\n\n" in pkg_info else ""
        if pkg_info is None or "Description-Content-Type: text/markdown" not in pkg_info or not body:
            drifts.append(Drift("package_pages", pyproject_file, None, f"{name}/PKG-INFO", None, f"build {name} and confirm PKG-INFO carries a text/markdown description"))
        h1 = readme_h1(readme)
        if h1 != name:
            drifts.append(Drift("package_pages", readme_file, 1, readme_file, 1, f"set the README H1 to {name}"))
        for line, sentence in alias_sentences(readme, ALIASES):
            if not any(marker in sentence.lower() for marker in DEPRECATION_MARKERS):
                drifts.append(Drift("package_pages", readme_file, line, readme_file, line, "state the alias is deprecated or drop it"))
        for _, sentence in alias_sentences(description, ALIASES):
            if not any(marker in sentence.lower() for marker in DEPRECATION_MARKERS):
                drifts.append(Drift("package_pages", pyproject_file, None, pyproject_file, None, "state the alias is deprecated in description or drop it"))
        if not description_ok(description):
            drifts.append(Drift("package_pages", pyproject_file, None, pyproject_file, None, "make description one sentence under 160 characters"))
        return drifts

    return [d for pkg in facts.get("packages", []) for d in package_drifts(pkg)]


def gather_page_facts(root: str, manifest: Mapping, run: Callable) -> dict:
    components = manifest.get("components", {})

    def package_facts(name: str) -> dict | None:
        component_dir = Path(release.component_dir(root, name))
        if not (component_dir / ".github" / "workflows" / "publish.yml").exists():
            return None
        pyproject_path = component_dir / "pyproject.toml"
        readme_path = component_dir / "README.md"
        with tempfile.TemporaryDirectory() as out_dir:
            run(["uv", "build", "--sdist", "--out-dir", out_dir], cwd=str(component_dir), capture_output=True, text=True)
            sdists = list(Path(out_dir).glob("*.tar.gz"))
            pkg_info = None
            if sdists:
                with tarfile.open(sdists[0]) as tf:
                    member = next((m for m in tf.getmembers() if m.name.endswith("PKG-INFO")), None)
                    pkg_info = tf.extractfile(member).read().decode() if member else None
        pyproject = tomllib.loads(pyproject_path.read_text()) if pyproject_path.exists() else {}
        readme = readme_path.read_text() if readme_path.exists() else ""
        return {"name": name, "pyproject": pyproject, "readme": readme, "pkg_info": pkg_info}

    return {"packages": [pkg for name in components for pkg in [package_facts(name)] if pkg is not None]}
