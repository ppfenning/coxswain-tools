"""Chair reader for `stranded`: the rows of `runs_stranded.stranded`, projected to five keys."""

from __future__ import annotations

from agent_tools import runs_stranded

KEYS = ("run", "task", "phase", "branch", "remedy")


def keep_stranded_keys(rows: list[dict]) -> list[dict]:
    """Plain dicts holding only `KEYS`, in input order."""
    return [{k: row.get(k) for k in KEYS} for row in rows]


def read_stranded(records: list[dict], items: list[dict]) -> list[dict]:
    """Edge. `records` and `items` come from the caller: their loaders live in cli.py, which this module does not import."""
    return keep_stranded_keys(runs_stranded.stranded(records, items))
