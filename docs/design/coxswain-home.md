# The coxswain home

Status: design for the install-workspace-and-settings epic, phase one. Date: 2026-10-16.

Goal: coxswain gets its own root after install, and no shipped file names a user's project folder. This document
answers five questions. Each has one decision and one reason. None is left open. Later phases build what is decided
here and change nothing about the decisions without a new design note.

Three terms are used throughout. The coxswain home is the one directory the installer owns. The config directory is
defined once, in question 2. A workspace is a user's folder of work that holds the cartridges folder, the queue and
the work files.

## 1. Install versus workspace split

Decision: the install lives under the coxswain home and the workspace is registered by location. The home holds the
three coxswain-* checkouts and the virtualenvs that sit beside them. A workspace stays where the user keeps it and is
recorded by the profile field workspace_dir. The launcher maps the harness path per host.

Reason: the installer owns what it can rebuild, while a workspace holds the user's own work and may sit beside their
project folders, so the root must not claim it.

### Consequences for the host set

The live host set is the active rows of the store hosts table. Each host's beat records its own workspace_dir and
checkouts, so the beat is the only source of a remote path. The profile lane_hosts block is a fallback, read only
when a host has no beat row.

launch_plan in agent_tools/remote_launch.py sends the chair's harness_dir to each lane host unchanged today. After
this design it takes the host's beat checkouts and maps the graphs checkout the way it already maps repo paths. Equal
roots on every machine are rejected because a macOS host and a Linux host resolve the home differently, and because
a half-migrated fleet has two roots at once. Mapping lets old and new hosts run side by side during the move. The
file remote_launch.py is therefore named under Files by phase.

The uv tool environments are not in the home. uv keeps them in its own tool directory, and the migration reinstalls
them from the moved checkouts.

## 2. XDG layout versus one dot-directory

Decision: use the XDG layout, with all three directories decided. The config directory does not honour
XDG_CONFIG_HOME and is always the tilde config folder. The data directory honours XDG_DATA_HOME and is the coxswain
home. The state directory honours XDG_STATE_HOME and holds migration state only.

Reason: eight modules hardcode the tilde config path to the profile and no phase here owns them, while data and
state are reached only through the resolver, so only those two can honour XDG without widening the file set.

### The three directories

The config directory is the tilde config folder. This is the one definition. Every phase that needs it calls the
resolver in agent_tools/home.py, and the resolver returns this value and ignores XDG_CONFIG_HOME. The existing readers
that hardcode the same folder already agree with it: DEFAULT_PROFILE and the garage environment path in cli.py,
run_store.py, dash_feed.py, dash_detail_health.py, dash_detail_release.py, inbox_cli.py, inbox_approvals.py and the
error text in route.py. They stay as they are. A later design that honours XDG_CONFIG_HOME must route all of them
through the resolver first.

The profile is read from the config directory. setup_install.profile_path keeps formatting the path as the
agent-tools folder under the config directory, so the profile does not move. AGENT_TOOLS_PROFILE, when set, names the
profile file directly and wins over the config directory.

The data directory is the coxswain subfolder of XDG_DATA_HOME, or the tilde local share folder when that is unset. A
relative or empty XDG value counts as unset, as the XDG specification says. The data directory is the coxswain home.
It holds an installs folder for the checkouts and virtualenvs, and a workspaces folder where setup install creates a
workspace when the user names none. Question 5 says how COXSWAIN_HOME overrides it.

The state directory is the coxswain subfolder of XDG_STATE_HOME, or the tilde local state folder when that is unset.
It holds the migration marker and nothing else. The stats ledger stays where stats_ingest.LEDGER_PATH puts it, under
the agent-graphs state folder, because the ledger is history and a rename would split it. The garage environment file
stays where it is for the same reason: it is read by operator scripts the migration does not own.

## 3. Marketplace location and the doctor rows

Decision: setup install records the marketplace location only in the Claude registry, through the claude command
line. It computes the path of the cartridges checkout under the home, lists the registered marketplaces with the
claude plugin marketplace list command, and adds the checkout with claude plugin marketplace add. When the listed
entry points elsewhere it runs claude plugin marketplace remove first and then add again. No settings file is read
or edited at any point.

Reason: the registry is the one place Claude reads, so a second record in the profile would only drift from it.

### Doctor rows

Two rows are added to doctor.py. Each has a one-line pass condition.

- Row "marketplace registered" passes when the claude marketplace list shows an entry for the cartridges marketplace.
- Row "marketplace location" passes when that entry's source text equals the cartridges checkout path under the
  resolved home, compared as text without following any link.

Both rows report not checked, not failed, when the claude command is absent from the path.

## 4. Migration of installs, workspaces and lane hosts

Decision: one subcommand, setup migrate, plans by default and changes things only when given the flag --apply. The
same flag spelling is used by other subcommands in cli.py, where the default lists the changes and --apply writes
them. Installs move into the home and stay reachable through a compatibility link. Workspaces stay in place and are
re-pointed. Lane hosts run the same subcommand on their own machine, and the chair's machine re-points its
lane_hosts entries from the hosts table.

Reason: a dry-run default with one apply flag is already the repository's idiom, and a second spelling would be a
second thing to learn.

### Path inventory

Earlier drafts each missed a stored location. These are all ten. The word migrate step means a step of setup migrate.

1. Profile fields cartridges_dir, skills_roots, provider_profile, harness_dir, workspace_dir and umbrella_dir.
   - New value: cartridges_dir stays, because it is a workspace folder and not the checkout. skills_roots and
     provider_profile point into the cartridges checkout under the home. harness_dir points at the graphs checkout
     under the home. workspace_dir and umbrella_dir stay unless the operator passes a new workspace.
   - Rewriter: a migrate step. The installer writes the same fields on a fresh install.
   - Verification: the doctor row "profile paths" checks the first five fields and each skills root, but it tests
     only that each path exists, so it passes through a compatibility link and cannot prove a re-point. The new row
     "home paths" proves it, see the next subsection. umbrella_dir has no row today, so the same phase adds it to
     "profile paths".
2. Profile lane_hosts workspace_dir, repo_map values and sources folders.
   - New value: each host's own workspace_dir and checkout paths as that host reports them in its active row of the
     store hosts table.
   - Rewriter: a migrate step in the lane-host planner. It runs on the chair's machine, where the lane_hosts block
     lives, and copies each reporting host's values into that host's entry. A host with no beat row keeps its entry
     and is listed in the closing report. Each lane host's own profile is rewritten by running setup migrate on that
     host, under item 1.
   - Verification: no doctor row checks these today. The new row "lane host paths" passes when every lane_hosts
     entry equals its host's reported values, and tests/test_setup_migrate_hosts.py covers the rewrite.
3. The workspace cartridges folder, where each team's base and local entries are relative symlinks into the
   cartridges checkout.
   - New value: relative symlinks recomputed from the workspace to the checkout under the home.
   - Rewriter: a migrate step. The links are relative, so a moved checkout leaves them dangling.
   - Verification: the new row "cartridge links" passes when the lexical target of every base and local entry equals
     the cartridges checkout path under the home. It does not follow the compatibility link, so it fails before the
     rewrite.
4. Editable installs by path: the graphs virtualenv, the tools virtualenv, the uv tool providing cox and the uv tool
   providing the cartridge command.
   - New value: each installs editable from the moved checkout. The graphs virtualenv and the cartridge tool use the
     cartridges checkout. The tools virtualenv and the cox tool use the tools checkout.
   - Rewriter: a migrate step. A virtualenv holds absolute paths in its scripts and its config file, so it is
     recreated at the new location and never patched. Each uv tool is reinstalled from the new path with its old
     environment replaced.
   - Verification: the new row "install bins" passes when the editable source recorded in each installed package's
     metadata equals the new checkout path as text. The row "profile paths" does not check any bin directory.
5. The coxswain-* checkouts and the worktree records naming them.
   - New value: the checkouts sit under the installs folder of the home. Worktrees stay where they are.
   - Rewriter: a migrate step moves each checkout and then repairs the records with git worktree repair, run from the
     new checkout and given each worktree path.
   - Verification: tests/test_setup_migrate.py asserts that git worktree list names no missing path afterwards, and
     the row "home paths" checks the checkout locations.
6. The Claude marketplace registry entry for the cartridges checkout.
   - New value: the cartridges checkout under the home.
   - Rewriter: the installer, through claude plugin marketplace remove and add. A migrate step calls the installer
     function and does not duplicate it.
   - Verification: the doctor row "marketplace location" from question 3.
7. Store hosts table rows with workspace_dir, repos and checkouts.
   - New value: whatever the host's next beat reports.
   - Rewriter: a migrate step. The final verify step on each host emits a beat, and the beat writes that host's row.
     No step edits the table directly, since the beat is the single writer of its row.
   - Verification: tests/test_setup_migrate_hosts.py asserts that the planner lists a host whose active row still
     names an old path and stops listing it after a beat reports the new one.
8. Queue rows and work files holding tilde-form repo paths.
   - New value: the tilde form of each moved checkout.
   - Rewriter: a migrate step that calls repo_path_migrate.py with the old and new prefixes.
   - Verification: tests/test_repo_path_migrate.py already covers the rewrite, and the migrate step rescans and
     reports zero rows with the old prefix.
9. The chair service unit on a Linux lane host, with workspace_dir in ExecStart and the graphs and tools virtualenv
   bin directories in PATH.
   - New value: the unit text regenerated from the new paths.
   - Rewriter: a migrate step on that host, followed by a daemon reload and a restart.
   - Verification: tests/test_setup_migrate_hosts.py compares the regenerated unit text.
   - Outside the migration: operator start and watchdog scripts and launchd jobs export the same bin directories. The
     migration does not edit them. Its closing report names each one it finds, so the operator edits them.
10. Home-anchored locations: the profile's default location, the ledger and the garage environment file.
    - New value: unchanged. All three stay, for the reasons in question 2.
    - Rewriter: none.
    - Verification: tests/test_home.py asserts the resolver returns the same profile and ledger paths as before.

The provider profile file holds no locations, so it is never rewritten. Only the profile field that points at it
changes.

One location is new in this design and is not an inventory item because it has no old value: the home record, a
one-line file beside the profile in the agent-tools folder of the config directory. Question 5 explains it. A migrate
step and the installer write it, and the row "home recorded" verifies it.

### Rows that prove a re-point

The old row "profile paths" and a symlink check that follows links both pass through the compatibility link, so
neither can show that a field was rewritten. Four new rows compare text and never follow a link.

- Row "home paths" passes when skills_roots, provider_profile and harness_dir each equal, as text after tilde
  expansion, a path under the resolved home.
- Row "cartridge links" passes as stated in item 3.
- Row "install bins" passes as stated in item 4.
- Row "home recorded" passes when the home record equals the resolved home.

These rows fail before the rewrite steps and pass after them. The step end states below use them for that reason.

### What moves, is re-pointed or stays

Moves: the three coxswain-* checkouts, into the installs folder of the home.

Is re-pointed: every profile field, the workspace symlinks, the editable installs, the marketplace entry, the queue
and work-file paths, the chair service unit and the chair's lane_hosts entries.

Stays: the workspaces, the worktrees, the profile file, the ledger and the garage environment file.

### Worktrees, leases and live runs

A checkout cannot move while a chair lease or a run holds one of its worktrees, because the run would lose its
working directory. The migration takes the lease on each of the three checkouts first, which stops the chair from
launching into them, and only then reads the holders. A check made before taking the lease could be overtaken by
the chair, so the read follows the acquisition. If any checkout is held, the migration releases what it took,
prints each holder and its lease, and moves nothing. The move is all or nothing across the three checkouts, so the
profile never points at a mix of old and new. The migration does not wait and does not stop a run. The operator reruns
--apply after the run ends. The leases are kept until the verify step ends, so the chair launches nothing into a
half-moved checkout.

### Safe order

The invariant: no old location is removed until every inventory item pointing at it is re-pointed and verified. The
old location stays valid through a compatibility link until every active host reports the new one. Each step has an
end state, and each step checks its end state before acting, so a rerun skips finished steps.

1. Plan. End state: the inventory is scanned, nothing is written and the plan is printed.
2. Preflight. End state: the leases on all three checkouts are held by the migration, no other lease or run holds a
   worktree, and the marker in the state directory records the old and new roots. Otherwise the leases are released
   and nothing has changed.
3. Move the checkouts. End state: all three checkouts sit under the home and a compatibility link at each old path
   resolves to it.
4. Repair worktrees. End state: git worktree list from each new checkout names no missing path.
5. Recreate the virtualenvs and reinstall the uv tools. End state: the row "install bins" passes.
6. Re-register the marketplace. End state: the row "marketplace location" passes.
7. Rewrite the profile fields and the workspace symlinks, and write the home record. End state: the rows "home
   paths", "cartridge links" and "home recorded" pass.
8. Rewrite the queue rows and work files. End state: a rescan finds no old prefix.
9. Regenerate the chair service unit on a Linux lane host. End state: the unit names the new paths and the service
   is active.
10. Verify. End state: every row that checks this machine passes, the leases are released and a beat reports the new
    workspace_dir and checkouts.
11. Re-point the chair's lane_hosts entries. End state: the row "lane host paths" passes. A host whose active row
    still reports an old path is listed and the step stops there, to be rerun later.
12. Report. End state: the closing report lists every operator script and launchd job that still names an old bin
    directory, and every lane host without a beat row.
13. Drop the links. End state: the compatibility links and the old virtualenvs are gone. This is the only step that
    removes an old location. It runs only when every active row of the store hosts table reports the new location and
    every row above passes, and otherwise it prints what is lagging and stops with the links in place.

### Half-finished moves

The marker in the state directory records the last finished step and the old and new roots. A half-finished move is
detected two ways. The doctor row "migration state" fails when the marker names an unfinished step. It also fails
when the inventory scan finds an item that resolves to the old root while a compatibility link or a new checkout
exists. The scan is authoritative and the marker is advisory, so a lost marker costs nothing.

Resume is a rerun of setup migrate with --apply. Steps are idempotent and test their end state first, so the run
continues at the first unfinished step. A rerun that finds some checkouts moved and some not takes the leases again
and finishes step 3 for the rest before it goes on. Steps 5 to 9 never start until all three checkouts have moved.

## 5. COXSWAIN_HOME

Decision: COXSWAIN_HOME overrides the coxswain home and nothing else. The config directory and the state directory
ignore it. The chosen home is recorded in the home record, so later invocations resolve to it.

Reason: the home is the one location a user may want on another disk, while the profile must stay findable from a
fixed place before any home is known.

### The home record

The home record is a one-line file named home in the agent-tools folder of the config directory. setup install and
the migration write it with the resolved home. It is not a profile field, because the profile parser ignores unknown
keys, and so a field would need a change in route.py that this design does not make. Without a record, a second
setup install or a doctor run without the original flag would resolve to the default home and make a second install.

### Precedence

The home resolves in this order. The first that is set and non-empty wins.

1. The --home flag on setup install or setup migrate.
2. COXSWAIN_HOME.
3. The home record.
4. The coxswain subfolder of XDG_DATA_HOME.
5. The coxswain subfolder of the tilde local share folder.

When the flag or the variable names a home that differs from the record, setup install stops and tells the operator
to run setup migrate. Setup migrate treats that home as the target and the record as the source. The doctor row "home
recorded" fails whenever the resolved home and the record differ.

The profile file resolves in this order.

1. AGENT_TOOLS_PROFILE.
2. The profile path under the config directory, which is the tilde config folder.

Only the installer, the migration and the doctor call the home resolver. Everything else reads the absolute paths in
the profile.

## Files by phase

Each later phase edits only the files below. No cartridges file changes. A change that must touch one is a separate
intake.

- Resolver phase: agent_tools/home.py, new, with tests/test_home.py. It returns the config directory, the home and
  the state directory, and reads and writes the home record.
- Installer phase: agent_tools/setup_install.py with tests/test_setup_install.py and tests/test_setup_install_cli.py.
- Doctor phase: agent_tools/doctor.py with tests/test_doctor.py. It adds the rows marketplace registered, marketplace
  location, home paths, install bins, cartridge links, home recorded, lane host paths and migration state, and
  extends profile paths to cover umbrella_dir.
- Migration planner phase: agent_tools/setup_migrate.py, new, with tests/test_setup_migrate.py.
- Lane-host planner phase: agent_tools/setup_migrate_hosts.py, new, with tests/test_setup_migrate_hosts.py.
- Launcher phase: agent_tools/remote_launch.py with tests/test_remote_launch.py, for the per-host harness_dir
  mapping decided in question 1.
- Subcommand wiring phase: agent_tools/cli.py with tests/test_cli.py, for the setup migrate subcommand and its
  --apply and --home flags.
