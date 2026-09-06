# coxswain-tools

`cox`, the operator command for Coxswain.

Deterministic tools the operator's shell calls instead of re-deriving by hand
what a run already recorded: usage, trace, cleanup, landing, the HUD, plans.
The mould, every time: a pure core with no I/O, the filesystem and the
network at the edges, dry-run by default where a write is involved.

## Install

```bash
uv tool install coxswain-tools
cox setup doctor
```

`agent-tools` is kept as an alias for one release.

## Commands

Bare `cox`, with no subcommand, opens the coxswain session: a real Claude
Code session with the `coxswain` plugin loaded, working directory at the
profile's `workspace_dir`.

**setup** — does this machine's profile actually work
```
cox setup                                       a small terminal UI over setup doctor and setup install (needs a terminal)
cox setup doctor [--profile PATH] [--json]      check this machine's profile against what it needs
cox setup install --root DIR --team T --workspace DIR [--plugins] [--hook] [--dry-run]   clone components and write a profile for this machine
```

**install / upgrade / versions** — coxswain's manifest
```
cox install --root DIR [--manifest PATH] [--dry-run]   clone/update coxswain components against the manifest
cox upgrade --root DIR [--manifest PATH] [--to VERSION] [--dry-run]   fetch and check out newer pinned versions; refuses dirty checkouts
cox versions [--root DIR] [--manifest PATH]     component versions against the manifest
```

**route** — file work for the harness, and see what is queued or running
```
cox route context [--profile PATH] [--json]     the routing profile's resolved context
cox route status [--profile PATH] [--json]      what is queued or running for this profile
cox route file --repo PATH --title TEXT [--body FILE|-] [--phase NAME] [--intake]   file a new ticket for the harness
cox route launch epic|decompose|cos [...]       run one of the harness's graphs directly
```

**runs** — what a harness run recorded, and cleaning up after it
```
cox runs usage RUN [--runs-dir runs] [--json]   usage stats and cost for one run
cox runs trace RUN [--role R] [-v]              the tool-call trace for one run
cox runs clean RUN --repo PATH [--apply]        delete a run's worktree and branches locally
cox runs land RUN --repo PATH [--task T] [--apply] [--no-merge]   merge a run's branch into the target repo
cox runs series [--runs-dir runs] [--json] [--append F]   per-run summary rows across a runs directory
cox runs events [--runs-dir runs] [--follow] [--json]   poll a run's log for structured events
cox runs top [--runs-dir runs] [--interval N] [--once]   live table of runs in flight
```

**usage** — the usage window and the pace it allows
```
cox usage assess [--json] [--runs-dir runs]       the pacing verdict for the current spend window
```

**epic** — a detached run
```
cox epic watch PIDFILE [--log LOG] [--json]        poll a detached run's pidfile until it exits
```

**plan**
```
cox plan serve DIR [--kind plan] [--check] [--no-open]   serve a visual plan through the local bridge
```

**hud** — the voice HUD's HTTP contracts
```
cox hud ops FILE|-                              post a batch of HUD operations
cox hud say TEXT [--persona P] [--voice V]      speak one line through the HUD voice
cox hud inbox show|arm|clear                    show, arm, or clear the HUD inbox
cox hud cast                                    broadcast an announcement to the HUD
```

## Maintainers

`cox dev` holds commands a maintainer of the Coxswain repositories runs;
nothing here is needed to use Coxswain.

```
cox dev release VERSION [--dry-run] [--manifest PATH] [--root DIR] [--checkout NAME=PATH] [--umbrella PATH]   the lockstep tag/bump-manifest/notes plan across coxswain's manifest, or (without --dry-run) tags and pushes every component
```

## Design

Every command follows the same rule: a pure core that takes plain data and
returns plain data, with the filesystem, the network and the clock pushed to
a thin edge, so a run's usage math, a route decision, a HUD payload is
testable with literals — no fixture run required.

## The profile

The `route` group reads one profile, `~/.config/agent-tools/profile.yaml`:
`team`, `cartridges_dir`, `skills_roots`, `provider_profile`, `harness_dir`,
`workspace_dir`, and `assume`, the gate answer detached runs are started
with. `--profile PATH` overrides the location for one command,
`AGENT_TOOLS_PROFILE` overrides it for a shell.

## Links

Docs: https://ppfenning.github.io/coxswain/
Umbrella repository (the whole fleet — cartridges, graphs, cast, HUD, tools): https://github.com/ppfenning/coxswain
