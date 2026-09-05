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

```bash
git clone https://github.com/ppfenning/agent-tools ~/repos/agent-tools
cd ~/repos/agent-tools && uv venv && uv pip install -e ".[dev]"
uv tool install -e .            # `agent-tools` on PATH for every seat
```

## Commands

```
agent-tools runs usage RUN [--runs-dir runs] [--json]   cost, turns, cache share — by role and by model
agent-tools runs trace RUN [--role build] [-v]          per node: turns, cost, tools, reads, whole-file reads, commands
agent-tools runs clean RUN --repo PATH [--apply]        the run's worktrees and scratch branches; phase branches kept; dry-run by default
agent-tools epic watch PIDFILE [--log LOG]              block until a detached run exits (or the cap), then the outcome lines
agent-tools hud ops FILE|-                              replace the HUD's ops list (id, label, status, persona?, detail?)
agent-tools hud say TEXT [--persona P] [--voice V]      speak a line through the HUD
agent-tools hud inbox show|arm|clear                    read, wait for, or clear directives
agent-tools hud cast                                    the seats the HUD's org ring shows
agent-tools plan serve DIR [--check] [--no-open]        lint, serve through the local bridge, open in Brave
agent-tools route context [--json]                      what a session reads at start: team, queued intake, runs in flight, initiatives with ready work; exits 0 even with no profile
agent-tools route status [--json]                       every run with a pidfile or a log: alive or exited, started, the log's outcome lines
agent-tools route file --repo PATH --title TEXT [--body FILE|-] [--phase NAME] [--intake]   write a one-task initiative, or with --intake an intake item; exit 2 if a target path exists or the profile is missing
agent-tools route launch epic --initiative DIR [--repo PATH] [--fix-attempts N] [--dry-run]   start the harness detached with a pidfile and log, and AGENT_GRAPHS_TRACE_DIR set to `<runs_dir>/<run-id>-trace` so every node writes a trace; exit 2 on a missing profile or harness venv, a missing initiative.md, a dirty repo, or a live run of the same initiative
agent-tools route launch decompose --idea FILE --initiative-id ID [--dry-run]   start the harness detached; exit 2 on a missing profile, harness venv, or idea file
agent-tools setup doctor [--profile PATH] [--json]      read-only: profile, paths, harness venv, cartridge, skills, provider, workspace — a table and an exit code
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
