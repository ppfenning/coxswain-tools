# Release discipline

> The `cox dev` code this describes (release, release-check, commands render) moved to the coxswain repository's unshipped `devtools/` package; from 0.15.0 it runs as `uv run --frozen python -m devtools <command>` from the coxswain checkout.

Status: approved by the chair 2026-09-15 under Pat's standing order (2026-09-08). Groups intake G6 from
`workspace/plans/agent-platform/2026-09-08-intake-grouping.md`:
`a-release-is-done-when-its-workflows-are-green`, `release-check-reports-36-false-drifts-on-a-clean-tree`.
Committed to `coxswain-tools` under `docs/design/`; the umbrella (`coxswain`) is the tree the checks run
over and gains nothing but what §1 names.

## 0. The defect, once

A release was called done because its tags existed. 0.4.0's tags were pushed on five repositories and
verified on the remotes; `coxswain-tools`' publish workflow then failed on "tag does not match pyproject
version" (tag 0.4.0, pyproject 0.3.0) and the package never reached PyPI; `releasable.yml` was invalid
YAML and had never run once. Both invisible from tag state. The gate that should have caught it,
`cox dev release-check`, cries wolf: 48 drifts on a clean tree, all false — 44 `cli_surface` against
per-group CLI pages that are GENERATED at docs-build time and never committed, 4 `manifest` against
component pages whose version is injected at build. So every release is cut with `--allow-doc-drift`, and
the one release with real drift will look identical.

> A release is done when the workflows its tags started are green; the tool bumps what it tags; and the
> release check compares against what the docs build actually produces, so a clean tree reads clean.

## 1. The tool bumps what it tags  (tools)

`agent_tools/release.py release_plan` gains, per component whose checkout has a `pyproject.toml`, a
`bump_pyproject` step before its `tag` step: set `[project] version` to the release version. Because
every component's default branch is protected, the bump is landed the way `cox runs land` lands: a branch
`release/<version>`, one commit `pyproject: bump to <version> to match the tag`, `push`, `pr_create`,
`wait_checks`, `merge` (squash) — reusing `_execute_land_step`'s step kinds in `agent_tools/cli.py`, not a
second executor. The umbrella's `manifest.toml` bump (`bumped_manifest_text`) travels the same road. Tags
are created only after every bump has merged, on the merged sha. `--dry-run` prints the full plan
including the bump PRs. A component whose pyproject already carries the version gets no bump step.

A `lockstep = false` component (pinned since tools #156) must not walk a private semver on its own: `release_plan`
checks `git rev-list <tag>..HEAD --count` on its default branch, and any commits past the tag turn its
`pinned` step into a `rejoin` — tag and push `v<version>` like a lockstep component, with
`bumped_manifest_text` rewriting its `tag` but leaving `lockstep = false`, so the next release pins it again.

## 2. The release waits on what it triggers  (tools)

After the tags are pushed, a `wait_workflows` step polls, per component, the workflow runs whose
`head_sha` is the tag's sha (`gh run list --commit <sha> --json status,conclusion,name,url`) until every
one has a conclusion or `timeout_s` (default 900) passes. Any conclusion other than `success` (or a
component with zero runs where its workflow files declare a tag trigger) fails the release with the run
URLs printed; the exit code is 2 — the release tool's refusal code throughout — and the message says which component and workflow. `workspace/bin/
verify-release.sh` stays as the independent after-the-fact check; this step is the release refusing to
call itself done.

Once a component's or the umbrella's `wait_workflows` succeeds, a `github_release` step publishes it —
run right after that component's `tag` or `rejoin`, or after the umbrella's own `tag_self`. `gh release
view <tag>` is run first; a zero exit means the release already exists and `gh release edit <tag>
--notes-file <path>` runs, anything else means it doesn't and `gh release create <tag> --verify-tag
--title "<title>" --notes-file <path>` runs instead, so a re-run never fails on a release already made.
The title is `coxswain-<name> <version>` for a component or `coxswain <version>` for the umbrella. The
umbrella's notes file is the whole of `docs/releases/<version>.md`; a component's is a temporary file
holding its own `## coxswain-<name>` section of that same page plus a line linking back to the umbrella's
release, or the line `unchanged since <tag>` (the component's previous tag) when no such section was
written for it. A `pinned` component gets no `github_release` step at all, since a later cut must never
rewrite its old release.

## 3. Version drift is a release-check drift  (tools)

`agent_tools/release_check.py` gains a `versions` check: each component checkout's `pyproject.toml`
version, and the umbrella's, must equal the manifest's `coxswain.version`; a mismatch is a `Drift` naming
the file, the two versions and the fix (`cox dev release <v>` performs the bump). `cox dev release`
refuses (existing `gate`) when this check drifts and no `--allow-doc-drift` reason is given — a version
drift is never allowable by reason, so it is excluded from `gate`'s allowance.

## 4. The check compares against the build, not the tree  (tools)

- `release_check_cli.check_cli_surface`: run the umbrella's generator (`docs/_cli.py`, pure core
  `parse_subcommands`) over the live `cox --help` into a temporary directory and compare the CLI surface
  against THAT output, not against `docs/reference/cli/*.md` in the tree (only `index.md` is committed).
  Its docstring states this. A missing generator or `cox` not on PATH is one drift saying so, not 44.
- `release_check_manifest.check_manifest`: a component page under `docs/components/<name>.md` must exist;
  a version string in the page, if any, must equal the component's own manifest tag (not the umbrella
  version); a page with no version string is not drift, since the site version comes from mkdocs' own
  `version:` provider at build, not from anything committed to the page. The join of root and umbrella name
  is fixed with a literal test whose root basename equals the component set's name (the doubled-path case
  from 2026-09-06).
- Postcondition, asserted by a test over a fixture tree mirroring the umbrella: a clean tree yields zero
  drifts from these two checks.

## 5. Out of bounds

Changing which components exist or how `cox install` resolves the manifest; the PyPI trusted-publisher
setup; `verify-release.sh`.

## 6. Rules, one literal test each

1. `release_plan` emits `bump_pyproject` + land steps for a component at a lower version and none for one
   already at the version; tags follow every merge in the plan order.
2. `wait_workflows` returns success only when every run for the tag sha concludes `success`; a `failure`
   names the component, workflow and URL; a tag-triggered workflow with zero runs after the timeout fails.
3. The `versions` check drifts on a pyproject that disagrees with the manifest and is not allowable by
   `--allow-doc-drift`.
4. `check_cli_surface` compares against the generator's output and reports one drift when the generator
   is unavailable.
5. `check_manifest` joins root and component name once; the version placeholder is substituted before
   comparison.
6. The umbrella fixture tree yields zero drifts on a clean checkout.
