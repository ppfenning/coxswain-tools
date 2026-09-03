# The routing layer — design

Make the harness the default for work. A live session that receives a work
request — anything that changes a repository — files it and runs it through
the harness under the active cartridge, instead of editing the repository
itself. Questions, checks, and operations stay conversational.

Three repositories change. Nothing in `agent-graphs` changes: the router is a
client of the harness's existing CLI, and a diff that touched `harness/` would
be governance.

| Piece | Repository | Kind |
|---|---|---|
| `agent-tools route` command group | agent-tools | deterministic: profile, intake, launch, status |
| `route-work` skill | agent-cartridges, `skills-plugins/local-skills` | judgment: is it work, which repo, what size |
| `route` role, marketplace manifest | agent-cartridges | the cartridge names the skill; the plugin installs |
| profile file, session hook, venvs | the machine's dotfiles | install, private values |

## 1. The profile

One file, `~/.config/agent-tools/profile.yaml`, is the only place a machine's
private bindings appear. Every command in the group reads it; nothing in this
repository or the skill names a team, a home path, or a repository.

```yaml
team: <cartridge name>
cartridges_dir: <dir the loader resolves --team in>
skills_roots: [<plugin roots for --skills-root>]
provider_profile: <path to a providers/*.yaml>
harness_dir: <agent-graphs checkout; shell.py and .venv live here>
workspace_dir: <dir holding intake/, work/, runs/>
assume: a          # gate answer for detached runs; drafts only ever land
```

Derived, never configured: `intake_dir = workspace_dir/intake`,
`work_dir = workspace_dir/work`, `runs_dir = workspace_dir/runs`. Paths are
expanded at the edge (`~` and env vars); the pure core sees strings.

The file is a flat YAML subset — `key: scalar` and `key: [a, b]` — parsed
by a small stdlib function, so this package keeps its empty dependency
list. A nested key or an unknown key is a parse error naming the line.

`--profile PATH` overrides the location on every subcommand, and
`AGENT_TOOLS_PROFILE` does the same for a shell.

## 2. `agent-tools route context`

Prints the routing contract a session reads at start. Stdout only; exit 0
always, so a session never fails to start because of it.

With a profile:

```
routing: team <team>; work requests go through the route-work skill, questions stay inline
intake: 2 queued — "<title>", "<title>"
runs: 1 in flight — <run-id> (pid 4242, since 14:02)
ready: <initiative> (3 tasks ready in phase <phase>)
```

Without one:

```
routing: no profile at ~/.config/agent-tools/profile.yaml; the harness is not configured on this machine
```

`--json` emits the same facts as a document. The renderer is pure over
parsed inputs: queued items (id, title), runs (id, pid, alive, started),
initiatives (id, phase, ready count).

## 3. `agent-tools route file`

Writes the item the harness will read. Two shapes, one flag apart.

```
agent-tools route file --repo PATH --title TEXT [--body FILE|-] [--phase NAME]
agent-tools route file --repo PATH --title TEXT [--body FILE|-] --intake
```

Without `--intake` it writes a **one-task initiative** into the work store:
`work/<slug>/initiative.md` (frontmatter `id`, `title`, `repo`) and
`work/<slug>/<phase>/<slug>.md` (frontmatter `id`, `phase`, `state: ready`,
`needs: []`, `surfaces: []`, `title`), body from `--body` or the title. The
phase defaults to `build`. The `repo` field is new to the initiative
frontmatter and is read only by this tool: `route launch epic` defaults
`--repo` from it. The harness ignores keys it does not know.

With `--intake` it writes `intake/<YYYY-MM-DD>-<slug>.md` with the same
frontmatter (`id`, `title`, `repo`) and body, for `decompose` to consume.

The slug is the title lower-cased, non-alphanumerics collapsed to `-`,
truncated to 48 characters. An existing path is refused, never overwritten.
Prints the path written.

## 4. `agent-tools route launch`

Assembles the harness command line from the profile and starts it detached.
This is the only place that command line exists.

```
agent-tools route launch epic --initiative DIR [--repo PATH] [--fix-attempts N] [--dry-run]
agent-tools route launch decompose --idea FILE --initiative-id ID [--dry-run]
```

The argv is

```
<harness_dir>/.venv/bin/python <harness_dir>/shell.py <graph>
  --team <team> --cartridges-dir <cartridges_dir>
  --skills-root <each root> --provider-profile <provider_profile>
  --runs-dir <runs_dir> --assume <assume> --run-id <run-id>
  <graph needs>
```

with `epic` adding `--initiative DIR --repo PATH` and `decompose` adding
`--idea FILE --initiative-id ID`. Both add `--workdir <workspace_dir>`, so
the apply arms land work items and state in the workspace. Any extra
arguments after `--` pass through untouched.

The child's `PATH` is the parent's with two entries prepended when they
exist: `<repo>/.venv/bin` (for `epic`) and `<harness_dir>/.venv/bin`. The
cartridge's check arm runs its commands with `shell=True` in a worktree and
inherits this environment; that is how `pytest -q` resolves to the target
repository's own interpreter without any check naming a path.

The run id is `<initiative or initiative-id>-<n>`, `n` the smallest integer
not already used by a file in `runs_dir` with that prefix. The process starts
in its own session (`start_new_session=True`), stdin closed, stdout and
stderr appended to `runs_dir/<run-id>.log`; its pid is written to
`runs_dir/<run-id>.pid`. The command prints three lines — `run <run-id>`,
`pid <path>`, `log <path>` — and returns.

Refused before anything starts, each with a one-line reason:

- no profile, or `harness_dir` without `shell.py` or `.venv/bin/python`
- for `epic`: the initiative directory has no `initiative.md`; the repo
  cannot be resolved (no `--repo` and no `repo:` in the frontmatter); the
  repo has uncommitted changes (`git status --porcelain` non-empty); a
  `.pid` for this initiative is alive
- for `decompose`: the idea file does not exist

`--dry-run` prints the argv and the paths it would write and starts nothing.

## 5. `agent-tools route status`

Lists runs in `runs_dir` that have a `.pid`: id, pid, alive or exited, start
time from the pidfile's mtime, and the log's outcome lines through the
existing `epic.summarize_log`. `--json` for the document. Read-only.

## 6. The `route-work` skill

A skill body in `skills-plugins/local-skills/skills/route-work/SKILL.md`,
bound in the `local` cartridge as the optional role `route`, so any team can
substitute its own. The base cartridge declares `route` in its optional
roles; the existing "every graph-facing role is bound" test keeps `local`
honest.

Discipline the body states:

- **Route work, answer everything else.** A request routes iff acting on it
  would change a repository. A question, a check, an operations task, or a
  request about the machine is answered inline, and the skill says so in
  one line when it declines.
- **Name the repository before anything else.** From the request, or the
  working directory when it is inside one. Ambiguity is a question to the
  person, not a guess.
- **Size against the cartridge's `epic_threshold`.** Below it (one phase,
  fewer than three tasks, one repository): `route file` writes the one-task
  initiative and `route launch epic` runs it. At or above it: `route file
  --intake`, then `route launch decompose`, then `route launch epic` once
  the tasks have landed.
- **Detach, watch, report.** After launching, arm `agent-tools epic watch
  PIDFILE --log LOG` in the background; re-arm while the pid lives. When it
  exits, report what landed, what was quarantined and why, the cost from
  `runs usage`, and the branch to open a pull request from. Merging to the
  default branch is never this skill's to do.
- **Never do the work inline after routing.** A routed request is the
  harness's; the session's job is to relay the outcome.

Failure modes the body names: routing a question; editing the repository
"while waiting"; guessing the repository; retrying a quarantined run
without reading why; reporting a run as landed from the log's shape rather
than its outcome lines.

## 7. Errors, end to end

| Condition | Where caught | Behaviour |
|---|---|---|
| no profile | every subcommand | `context` prints the one-liner and exits 0; the rest exit 2 with the path expected |
| harness venv missing | `launch` | exit 2, names the path and the install command from the harness README |
| dirty target repo | `launch epic` | exit 2, prints the porcelain lines |
| initiative already running | `launch` | exit 2, names the live run id |
| watcher hit its cap | skill | re-arm; not a failure |
| run quarantined | skill | reported with the reasons; never retried on its own |
| item path exists | `file` | exit 2; nothing overwritten |

## 8. Tests

`agent_tools/route.py` is a pure core: `parse_profile`, `render_context`,
`harness_argv`, `next_run_id`, `slugify`, `initiative_files`,
`intake_file`, `status_rows`. Each is unit-tested against fixtures; none
touches a filesystem. The CLI's I/O edge — reading the profile, listing
`runs_dir`, spawning — is exercised with a temporary directory and a fake
`shell.py` that records its argv and exits, so no test starts a harness.

In agent-cartridges, the existing plugin test verifies `route-work` resolves
from the `local` cartridge, the governance test keeps the skill free of any
employer, and a new test asserts the marketplace manifest lists the plugin
at its real path.

## 9. Install

The machine's dotfiles setup gains four idempotent steps: write the profile
from variables at the top of the script; add the agent-cartridges checkout
as a local plugin marketplace and install `local-skills`; merge a
`SessionStart` hook running `agent-tools route context` into the Claude
Code settings without clobbering existing hooks; create the three
virtualenvs and `uv tool install` agent-tools. These have no test suite and
are done by hand, after the two repositories' changes land.

## Out of scope

A scheduled chief-of-staff over the intake queue (a timer running `cos`)
is a later addition and does not change this design. Journal entries and
HUD posts on completion belong to the scribe seat, not to the router.
