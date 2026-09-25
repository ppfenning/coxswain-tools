# The command table

> The `cox dev` code this describes (release, release-check, commands render) moved to the coxswain repository's unshipped `devtools/` package; from 0.15.0 it runs as `uv run --frozen python -m devtools <command>` from the coxswain checkout.

`agent_tools/cli.py`'s `build_parser` hand-builds argparse: 60 `add_parser(`
calls across `runs`, `stats`, `usage`, `epic`, `plan`, `route`, `courier`,
`install`/`upgrade`/`versions`, `dev`, `release`, `home`, `setup`. Each
command's help, args, and handler are written once for argparse, then
again by hand wherever else documented. This spec replaces the
hand-built parser with a table: `Command` rows in a new module,
`agent_tools/commands.py`, that argparse, the plugin's slash-command
files, and the README all render from.

## The `Command` row

```python
@dataclass(frozen=True)
class Command:
    name: str
    group: str
    summary: str
    args: tuple[Arg, ...]
    handler: Callable[[Namespace], int]
    slash: bool
    examples: tuple[str, ...]
```

`name` is the leaf subcommand (`"trace"`, not `"runs trace"`); `group` is
the top-level word (`"runs"`). `args` is a tuple of argparse-argument
descriptions (flag, positional, type, default, help). `handler` is the
same `fn` `set_defaults(fn=...)` wires up today. `slash` marks a command
that also ships as a plugin slash-command; most `dev` and `route` commands
are operator-only, `slash=False`. `examples` feeds both the plugin page
and the README.

## Generating `build_parser`

`build_parser` becomes a fold over `list[Command]`: group rows by `group`,
create one `sub.add_parser(group)` per group and one
`group_sub.add_parser(row.name)` per row, calling `add_argument` once per
`Arg`. The table is the only place a command's shape is declared; the
parser is its rendering, not a second source.

## Rendering the plugin's slash-commands

`cox dev commands render` is a new subcommand, itself a `Command` row in
the `dev` group — unavailable until dev's ticket lands. It filters the
table to `slash=True` rows and, per cartridge under
`skills-plugins/commands/`, writes one `<name>.md` per row from `summary`,
`args`, and `examples`, the same fields feeding argparse's help. Once
it exists, the plugin page and `--help` can no longer drift from each
other, only both from the table; before then, `slash=True` commands
already migrated in `runs` and `route` keep hand-maintained pages, caught
up by dev's first render run.

## Rendering the README

The same renderer, given the full table instead of the `slash=True`
filter, produces the README's command-reference section: one line per
`Command`, in table order, with `name`, `summary`, and the first
`examples` entry. `cox dev commands render` grows a `--target readme` mode
writing this section between two marker comments, not a second hand-kept
renderer.

## Groups and the bare launcher

The five groups this migration covers are `runs`, `route`, `dev`, `setup`,
`stats`; each is a value of `Command.group`. The bare `cox` launcher —
today's top-level groups named in the intro — becomes the set of distinct
`group` values in the table plus any group not yet migrated, hand-built
until its ticket lands. `build_parser` merges generated groups with
whatever hand-built groups remain, so a partial table still produces a
correct parser.

## Migration order

One group per follow-up ticket, in this order: **runs**, **route**,
**dev**, **setup**, **stats**. Runs is first: the group the harness
exercises hardest, so a broken fold shows up at once. Route is second, not
last: by `add_parser` count it is the largest and most nested group — 18
calls against runs' 12, stats' 8, dev's 4, setup's 3 — proving the fold
against the widest shape early. Dev is third: its ticket adds
`cox dev commands render`, landing right after runs and route so that
render's first run catches up their slash pages instead of leaving them
stale. Setup and stats, smaller and adding no mechanism, close the
order. Each ticket moves one group into the table, points `build_parser`
at it instead of hand-building it, and is done only when a `--help`
snapshot test proves the output byte-identical to today's — an exact
string comparison. A ticket-filer can file "migrate `route`" the moment
"migrate `runs`" lands, without re-deriving why.

## `cli_surface` after the table

`release_check_cli.py`'s `check_cli_surface` today walks `cox --help`
recursively, diffing it against a separate umbrella generator's docs and
each component's README, because the parser and the docs are two
hand-maintained things that can drift. Once every group is in the table,
that drift is structurally impossible: the parser and the README section
are two renderings of the same rows. `cli_surface` shrinks to one check —
the README's command-reference section, re-rendered from the current
table, matches what is committed — dropping `doc_commands`,
`readme_commands`, `_namespaces`, and the recursive `--help` walk: no
second hand-built surface remains to compare against.
