"""The `quarantined` reader for `chair_facts.FactsDeps`: open quarantined work items as rows."""
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from agent_tools import route, run_store

QUARANTINED = "quarantined"

Row = dict[str, str]


def _row(path: str) -> Row | None:
    """Only `work/<initiative>/<phase>/<task>.md` has a row; the task is the file stem."""
    p = PurePosixPath(path)
    if len(p.parts) == 4 and p.parts[0] == "work" and p.suffix == ".md":
        return {"initiative": p.parts[1], "phase": p.parts[2], "task": p.stem}
    return None


def quarantined_rows(items: Iterable[tuple[str, str]]) -> list[Row]:
    """Rows for (path, current state) pairs in state `quarantined`, in input order."""
    rows = (_row(path) for path, state in items if state == QUARANTINED)
    return [row for row in rows if row is not None]


def read_quarantined(root: Path, mode: str) -> list[Row]:
    """Edge. The store is read, and overrides file state, only under mode "store", as `cli._stored_work_items` does."""
    items = [
        route.work_item(route.parse_frontmatter(p.read_text())[0], initiative=p.parts[-3], phase_dir=p.parts[-2], stem=p.stem)
        for p in sorted(root.glob("work/*/*/*.md"))
    ]
    stored = route.with_store_states(items, run_store.work_items(root / "runs") if mode == "store" else [], mode)
    return quarantined_rows((f"work/{item['initiative']}/{item['file']}", item["state"]) for item in stored)
