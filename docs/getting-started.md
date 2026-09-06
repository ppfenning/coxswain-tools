# Getting started: from three clones to a verified first run

The routing layer spans three repositories and one profile file. This is the
order a person on a machine that is not the author's follows, top to bottom,
to a first verified run.

## 1. What you are installing

`agent-cartridges` holds each team's rules and the skill bodies a run reads,
and doubles as a local plugin marketplace for skill-aware providers.
`agent-graphs` is the harness that runs a graph; it depends on
agent-cartridges as a path dependency, so a working agent-graphs checkout
needs a resolvable agent-cartridges checkout beside it.
`agent-tools` is the CLI a seat runs instead of reasoning by hand; its
`route` group is the entry point that files work and launches the harness.
Clone them in dependency order: cartridges, then graphs, then tools, side
by side under one checkout root.

## 2. Clone and create the environments

```bash
git clone https://github.com/ppfenning/agent-cartridges <checkout root>/agent-cartridges
git clone https://github.com/ppfenning/agent-graphs <checkout root>/agent-graphs
git clone https://github.com/ppfenning/agent-tools <checkout root>/agent-tools
```

In each of the three, create the environment:

```bash
cd <checkout root>/agent-cartridges && uv venv && uv pip install -e ".[dev]"
cd <checkout root>/agent-tools && uv venv && uv pip install -e ".[dev]"
```

agent-graphs depends on agent-cartridges as a path dependency, so `uv sync`
fails there; the form that works installs both in one call:

```bash
cd <checkout root>/agent-graphs && uv venv && uv pip install -e ".[dev]" -e <checkout root>/agent-cartridges
```

Then put `agent-tools` on PATH for every seat:

```bash
uv tool install -e <checkout root>/agent-tools
```

## 3. The two interactive logins

Two CLIs need a one-time interactive login before a run can use them
headless:

- The provider CLI named by the profile's `command:` line. For the shipped
  Claude Code provider profile, that's `claude`. Run it once interactively
  and log in, because the harness runs it headless later and cannot answer a
  login prompt.
- `gh auth login`. The harness itself never pushes and never opens a pull
  request: a finished run leaves branches in the target repository. Opening
  the pull request from a landed branch is a step a person or a seat runs
  afterwards with the forge CLI, so it needs to be logged in too.

## 4. The profile

The route group reads one profile, `~/.config/agent-tools/profile.yaml`,
with these keys:

```yaml
team: <team>
cartridges_dir: <checkout root>/workspace/cartridges
skills_roots: [<checkout root>/agent-cartridges/skills-plugins]
provider_profile: <checkout root>/agent-cartridges/providers/claude-code.yaml
harness_dir: <checkout root>/agent-graphs
workspace_dir: <checkout root>/workspace
assume: a
```

`cartridges_dir` is where the TEAM's cartridge lives (the workspace's
`cartridges/`, see section 5), not the agent-cartridges checkout;
`skills_roots` and `provider_profile` point into that checkout. `assume` is
the gate answer detached runs start with (`a` approves what the gate would
ask, `r` refuses); it defaults to `a` when the key is absent and is passed
through unchanged to the harness.

`--profile PATH` overrides the profile location for one command;
`AGENT_TOOLS_PROFILE` overrides it for a shell.

## 5. The workspace

`workspace_dir` is a git repository with:

- `work/`: initiatives and their phases, one file per task.
- `runs/`: the pidfile, log, `<run-id>-trace` directory, and usage and
  manifest files a run leaves behind.
- `intake/`: tickets filed but not yet scoped onto a board.
- `cartridges/<team>/`: this team's cartridge, extending `local`, beside
  symlinks to `base` and `local` from the agent-cartridges checkout.

Create the workspace once, then the team cartridge with agent-cartridges'
own command, which also prints the two profile lines for section 4:

```bash
mkdir -p <checkout root>/workspace/{work,runs,intake,cartridges}
cd <checkout root>/workspace && git init
<checkout root>/agent-cartridges/.venv/bin/cartridge init <team> --cartridges-dir <checkout root>/workspace/cartridges
```

See agent-cartridges' README for what a cartridge is; it is not repeated
here.

## 6. The skills as a plugin (optional but recommended)

Provider-specific, and only for the Claude Code provider:

```bash
claude plugin marketplace add <checkout root>/agent-cartridges
claude plugin install local-skills@agent-cartridges
```

## 7. Verify

```bash
cox setup doctor
```

is read-only: it checks profile, paths, harness venv, cartridge, skills,
provider, and workspace, prints a table, and exits 0 when the layer is
ready to launch a run. `--profile PATH` and `--json` both work here too.

## 8. A first run

Run these from inside `workspace_dir`. `route launch epic --initiative`
resolves its argument against the current directory, and `runs usage` and
`runs series` default `--runs-dir` to the relative path `runs`.

```bash
cd <checkout root>/workspace
cox route file --repo <path> --title "..." --body FILE
cox route launch epic --initiative work/<slug> --fix-attempts 2
cox route status
cox runs usage <run-id>
cox runs series
```

`route file` prints the path it wrote for `<slug>`; use that printed value,
since `--title` is slugified (lower-cased, non-alphanumerics collapsed to
`-`, truncated to 48 characters) and will not match the title text
verbatim.

A finished run leaves behind its phase branches and a scratch worktree.
Under `runs/` it also leaves a pidfile, a log, a `<run-id>-trace` directory,
and the usage and manifest files that `runs usage` and `runs series` read
back.

## 9. Edit your cartridge

`cox setup` opens a menu; choose `edit cartridge`. It needs `root`,
`team`, and `workspace` filled in first, because the probe that reads your
cartridge builds its interpreter path from `root`.

The screen lists one row per editable key of your RESOLVED cartridge: its
value, and which layer it came from (`base`, `local`, your team, or
`edited`, once you have written something). A row you cannot edit is
marked read-only and says which layer owns it instead.

```
  policy.review_tier = 2 [team]
* crew.reviewer.enabled = true [edited]
  crew.reviewer.skills = review_charter (read-only: base)
```

Keys: `j`/`k` move between editable rows; `space` cycles a choice row
through its values; `e` opens a text row for typing, `ENTER` commits the
typed text, `ESC` cancels the edit (while editing, `q` is an ordinary
character, not the quit key); `u` reverts the row under the cursor to its
last-probed value; `w` writes; `r` re-probes without writing; `q` quits.

`w` writes ONLY `<team>/cartridge.d/edited.yaml`, merging your pending
edits with whatever fragment was already there, then re-probes so the
rows on screen reflect the loader's own view, not your guess at it. A
refused write keeps your pending edits and reports the loader's error on
the section the write touched. Writes are tighten-only: loosening a risk
or a ramp the base cartridge already declared is refused before anything
touches disk.

Starting from no team cartridge at all? Run `cartridge init <team>` first
(section 5).
