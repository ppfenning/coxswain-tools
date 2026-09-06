# agent-tools

Deterministic tools the agent seats call **instead of spending tokens**. Anything
a seat would otherwise do by reading a file wholesale and reasoning about it —
summing a run's cost, counting what a traced node did, cleaning up after a run,
posting to the HUD, serving a plan — is a function here that reads it and answers.

The mould, every time: a pure core with no I/O, the filesystem and the network
at the edges, dry-run by default where a write is involved, and tests that never
touch a real run.

## The fifth repository

| Repository | Owns |
|---|---|
| [`agent-cartridges`](https://github.com/ppfenning/agent-cartridges) | who a run works for |
| [`agent-graphs`](https://github.com/ppfenning/agent-graphs) | what runs, and the harness that runs it |
| [`agent-cast`](https://github.com/ppfenning/agent-cast) | who speaks |
| [`agent-voice-hud`](https://github.com/ppfenning/agent-voice-hud) | where you hear and see it |
| **`agent-tools`** | what the seats run so they do not have to think |

Tools that read agent-graphs' records and post to agent-voice-hud belong to
neither; a seat routes to them by name the way it routes to skills. Nothing here
names an employer, a tracker, or a person — CI refuses it.

## Install

See [`docs/getting-started.md`](docs/getting-started.md) for the full setup, from cloning all three repositories to a verified first run.

Not yet on PyPI: once the first tag ships, this package will be `coxswain-tools`, installable as `pip install coxswain-tools` or `uv tool install coxswain-tools`.

```bash
git clone https://github.com/ppfenning/coxswain-tools ~/repos/coxswain-tools
cd ~/repos/coxswain-tools && uv venv && uv pip install -e ".[dev]"
uv tool install -e .            # `agent-tools` on PATH for every seat
```

## Commands

Bare `cox`, with no subcommand, opens the coxswain session: a real Claude Code
session with the `coxswain` plugin loaded, working directory at the profile's
`workspace_dir`. `agent-tools` still works this release as an alias for `cox`.

```
cox runs usage RUN [--runs-dir runs] [--json]   cost, turns, cache share — by role and by model
cox runs trace RUN [--role build] [-v]          per node: turns, cost, tools, reads, whole-file reads, commands
cox runs clean RUN --repo PATH [--apply]        the run's worktrees and scratch branches; phase branches kept; dry-run by default
cox runs land RUN --repo PATH [--task T] [--apply] [--no-merge]   plan and land an approved run: pick branch, cherry-pick, PR, merge on green, clean; dry-run by default
cox runs events [--runs-dir runs] [--follow] [--json]   tail a run's log, trace and usage files as a live event stream
cox runs top   — live table of runs in flight (next task wires the screen)
cox usage assess [--json] [--runs-dir runs]     the pacing verdict for the current spend window against the resolved `policy.pacing.json` in --runs-dir, or the unmeasured default when it is absent
                                                 prints the one-line reason (or the full Assessment as JSON); exit 0 go/go_degraded, 3 hold, 4 stop
cox epic watch PIDFILE [--log LOG]              block until a detached run exits (or the cap), then the outcome lines
cox hud ops FILE|-                              replace the HUD's ops list (id, label, status, persona?, detail?)
cox hud say TEXT [--persona P] [--voice V]      speak a line through the HUD
cox hud inbox show|arm|clear                    read, wait for, or clear directives
cox hud cast                                    the seats the HUD's org ring shows
cox plan serve DIR [--check] [--no-open]        lint, serve through the local bridge, open in Brave
cox route context [--json]                      what a session reads at start: team, queued intake, runs in flight, initiatives with ready work; exits 0 even with no profile
cox route status [--json]                       every run with a pidfile or a log: alive or exited, started, the log's outcome lines
cox route file --repo PATH --title TEXT [--body FILE|-] [--phase NAME] [--intake]   write a one-task initiative, or with --intake an intake item; exit 2 if a target path exists or the profile is missing
cox route launch epic --initiative DIR [--repo PATH] [--fix-attempts N] [--dry-run]   start the harness detached with a pidfile and log, and AGENT_GRAPHS_TRACE_DIR set to `<runs_dir>/<run-id>-trace` so every node writes a trace; exit 2 on a missing profile or harness venv, a missing initiative.md, a dirty repo, or a live run of the same initiative
cox route launch decompose --idea FILE --initiative-id ID [--dry-run]   start the harness detached; exit 2 on a missing profile, harness venv, or idea file
cox route launch cos [--dry-run]   start the chief of staff detached: it reads intake and runs, dispatches within the bound, and consumes what it dispatched
cox setup   a small terminal UI over setup doctor, setup install and cartridge init (needs a terminal)
cox setup doctor [--profile PATH] [--json]      read-only: profile, paths, harness venv, cartridge, skills, provider, workspace — a table and an exit code
cox setup install --root DIR --team T --workspace DIR [--plugins] [--hook] [--force-profile] [--dry-run]   venvs, agent-tools on PATH, the profile, optionally the provider plugin and a session-start hook; dry-run prints the plan
cox install --root DIR [--manifest PATH] [--provider NAME] [--with FLAG] [--team T] [--workspace DIR] [--dry-run]   the plan over coxswain's manifest.toml: clone, fetch, skip or refuse per component, then setup_install, doctor, desktop; --dry-run only prints it, otherwise it runs each step and exits 0 only if every step ran clean
cox upgrade --root DIR [--manifest PATH] [--provider NAME] [--with FLAG] [--team T] [--workspace DIR] [--to VERSION] [--dry-run]   same plan as install, but refuses (exit 2, naming the directory) if any present checkout is dirty; --to overrides every component's pinned tag for this run
cox versions [--root DIR] [--manifest PATH]     pinned vs. installed tag per component, and status: ok, drift, missing, extra
```

## Maintainers

`cox dev` holds commands a maintainer of the coxswain repositories runs; nothing
here is needed to use Coxswain. `cox release` is a one-release alias that prints
`moved: use cox dev release` and exits 2.

```
cox dev release VERSION [--dry-run] [--manifest PATH] [--root DIR] [--checkout NAME=PATH] [--umbrella PATH]   the lockstep plan: tag every component, bump the manifest (skipped on a first cut of the declared version), notes, tag_self; exit 2 on refuse (bad semver, an existing tag, a lesser version, or a checkout that is not a ppfenning/coxswain remote); without --dry-run, executes only a first-cut plan — tags and pushes every component and the umbrella in turn, refusing before tagging anything if a checkout is dirty, off its default branch, the release note is missing, or the plan still carries a bump_manifest step (bump and commit the manifest by hand first)
```

Every command that reads a record is pure over parsed data and unit-tested
against fixtures; every command that writes is dry-run unless `--apply`.

The `route` group reads one profile, `~/.config/agent-tools/profile.yaml`:
`team`, `cartridges_dir`, `skills_roots`, `provider_profile`, `harness_dir`,
`workspace_dir`, and `assume`, the gate answer detached runs are started
with. `--profile PATH` overrides the location for one command,
`AGENT_TOOLS_PROFILE` overrides it for a shell, and the default path is read
when neither is set. Without a profile, `route context` prints one line and
exits 0; `file`, `launch` and `status` exit 2 and name the path they looked
for. `--dry-run` on either `launch` prints the argv, the pidfile and the log
path and starts nothing.

## Why this exists

One day of live epics found every defect by reading a usage file or a trace,
by hand, in a chief-of-staff's own turns: summing costs, counting Bash calls,
noticing a node read a 1,900-line file whole. Each of those readings cost
tokens and produced the same answer every time. They are functions now, and
the steward seat runs them.
