"""Pure card-key helpers: which store row owns a card, and is a title a duplicate."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

RowKeys = tuple[int | None, str | None, str | None]

_NON_ALNUM = re.compile(r"[\W_]+")


def normalized_title(title: str) -> str:
    """Lowercase, runs of non-alphanumerics (unicode-aware) become one space, ends trimmed."""
    return _NON_ALNUM.sub(" ", title.lower()).strip()


def _issue_from_extra(row: Mapping[str, Any]) -> int | None:
    extra = row.get("extra")
    issue = extra.get("issue") if isinstance(extra, Mapping) else None
    if isinstance(issue, bool):
        return None
    if isinstance(issue, int):
        return issue
    if isinstance(issue, str) and issue.isascii() and issue.isdigit():
        return int(issue)
    return None


def row_keys(row: Mapping[str, Any]) -> RowKeys:
    """(issue_number, project_item_id, parent_item); issue falls back to extra["issue"]."""
    issue_number = row.get("issue_number")
    resolved = issue_number if issue_number is not None else _issue_from_extra(row)
    return (resolved, row.get("project_item_id"), row.get("parent_item"))


def find_row(
    rows: Sequence[Mapping[str, Any]],
    issue_number: int | None,
    project_item_id: str | None,
) -> Mapping[str, Any] | None:
    """First row matching the issue number, else first matching the non-empty item id."""
    by_issue = next((r for r in rows if row_keys(r)[0] == issue_number), None) if issue_number is not None else None
    if by_issue is not None:
        return by_issue
    if project_item_id:
        return next((r for r in rows if row_keys(r)[1] == project_item_id), None)
    return None


def find_duplicate(title: str, open_issues: Sequence[Mapping[str, Any]]) -> int | None:
    """Number of the first open issue whose normalized title matches; empty never matches."""
    wanted = normalized_title(title)
    if not wanted:
        return None
    return next(
        (issue.get("number") for issue in open_issues if normalized_title(issue.get("title") or "") == wanted),
        None,
    )
