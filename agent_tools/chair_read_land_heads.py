"""Chair reader for the current tip of each refused phase branch."""

from collections.abc import Callable, Mapping, Sequence

from agent_tools.chair_read_land_conflicts import _rev_parse


def read_land_heads(
    refusals: Sequence[Mapping[str, object]],
    repos: Mapping[str, str],
    rev_parse: Callable[[str, str], str | None] = _rev_parse,
) -> dict[str, str]:
    """Edge. `<initiative>/<phase>` to the tip of `epic/<initiative>/<phase>`, for refused phases only; an unknown repo or an unresolved branch is omitted."""
    phases = dict.fromkeys((str(r["initiative"]), str(r["phase"])) for r in refusals)
    heads = {f"{i}/{p}": rev_parse(repos[i], f"epic/{i}/{p}") for i, p in phases if repos.get(i)}
    return {key: head for key, head in heads.items() if head}
