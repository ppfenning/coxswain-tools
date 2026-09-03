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
```

Every command that reads a record is pure over parsed data and unit-tested
against fixtures; every command that writes is dry-run unless `--apply`.

## Why this exists

One day of live epics found every defect by reading a usage file or a trace,
by hand, in a chief-of-staff's own turns: summing costs, counting Bash calls,
noticing a node read a 1,900-line file whole. Each of those readings cost
tokens and produced the same answer every time. They are functions now, and
the steward seat runs them.
