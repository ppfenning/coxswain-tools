# Router and steward (P4) design

Status: spec for review. This is the contract every later task in this initiative is
judged against. It states facts a builder can implement against, not aspiration.

## 1. `select_tier` contract

The router is one pure function: `select_tier(role, stats_window, policy) -> (tier,
reason)`. It reads no file, calls no clock, and makes no network call; every input it
needs arrives as an argument.

The `router: off|shadow|on` flag lives in the ROUTING profile, the file `route.parse_profile`
reads and `--profile` names, beside `provider_profile`, not inside it.
`agent_tools/cli.py`'s `_bounds_ceiling_for` resolves a role's ceiling by
following the routing profile's `provider_profile` field into that profile's
`tier_overrides` and `defaults`; the router flag sits at the routing-profile end of that
chain.

`stats_window` carries four fields per role. `landed_rate` is a float measured over the
window. `n` counts every call of that role in the window, challenger calls included, so the section 2 schedule lands on every Nth call. `start` and `end` mark the
window's bounds.

`policy` carries four fields: `default_tier`, `min_n`, `deviation_floor`, `challenger_n`.
`default_tier` is the profile's declared tier for the role. `min_n` is 20. `deviation_floor`
is 0.70. `challenger_n` is 10.

`select_tier` decides in this order. First, the challenger check of §2 runs regardless of
`min_n`, so exploration never waits on evidence. Second,
`n < min_n` returns `(default_tier, "insufficient_n")`. Third, `landed_rate <
deviation_floor` returns one tier above `default_tier` with reason `"landed_rate_low"`.
Otherwise it returns `(default_tier, "default")`.

## 2. Challenger rule

The challenger schedule is deterministic, keyed on `stats_window["n"]`. An eligible role
is a challenger exactly when:

    n > 0 and n % policy["challenger_n"] == 0

With `challenger_n = 10`, this fires on the 10th, 20th, 30th call of that role in the
window. No random draw, clock read, or external counter decides it; the same inputs
always produce the same decision. This check precedes the `min_n` gate of §1, so it can
fire on the role's 10th call even though `min_n` is 20.

`build` and `arbitrate` are never eligible. `build`'s output is the patch under review;
`arbitrate`'s output is the verdict that ends the loop. A downgraded call on either
contaminates the artifact the process depends on trusting, so exploration stays confined
to roles whose output is reviewed before it carries that weight.

A challenger call runs one tier below `default_tier`. Since the signature returns only
`(tier, reason)`, challenger status travels entirely in `reason`: `select_tier` returns the
fixed literal string `"challenger"`, not a prefix. The CLI edge matches that exact string
to set the `challenger` column, defined in section 3 of run-stats-store.md, on the call row.

## 3. A challenger failure is not a regression

Rows written with `challenger = true` are excluded from the landed-rate aggregate that
`select_tier` reads to build `stats_window`. Because a challenger row never enters
`landed_rate`, its outcome can never move `default_tier`, a ceiling, or trigger the
`deviation_floor` rule of §1, which reacts only to the floor tier's own landed_rate. A
challenger's outcome can still shape future sampling once it accumulates into a steward
proposal of section 5. The `challenger` boolean, described in section 6 of run-stats-store.md, keeps such a row
distinguishable from an ordinary regression; without it the first challenger failure reads
as the floor tier failing and exploration is switched off.

## 4. `cox steward propose`

`cox steward propose` reads `stats.db` through the query surface of run-stats-store.md
§5. It never edits a provider profile. It emits one proposal per finding as an intake
file, reusing `agent_tools/route.py`'s `intake_file` frontmatter shape:
`id`, `title`, `repo`, plus a body carrying the measured table that justifies it: role,
model, tier, n, and the landed rates compared.

Four proposal kinds exist: raise a ceiling, lower a ceiling, change a default tier, retire
a per-model override. Ticket 4's task builds ceiling proposals only, raise and lower; the
other two kinds are named here so the schema and CLI shape leave room for them, and are
out of scope for that task.

## 5. Evidence bar

A proposal may be emitted only when all of the following hold over a window capped at 14
days or 20 runs, whichever completes first: the challenger tier has `n >= 20` calls,
matching cost-bounds.md §2's rule that `n < 20` yields `insufficient`. The landed-rate
delta between the challenger tier and the floor tier must be at least 5 points:

    n_challenger >= 20 and abs(landed_rate_challenger - landed_rate_floor) >= 0.05

Below this bar `cox steward propose` emits nothing for that (role, model) pair rather than
a low-confidence proposal.

## 6. How a proposal closes

A proposal closes when a human reviews its intake file and opens a PR against the
provider profile, naming the proposal's intake filename so the edit traces back to the
measured table that justified it. `cox steward propose` writes the proposal; it does not
open the PR or touch the profile.
