<!-- Implementation contract for the run stats store (P0).

The narrative design lives in the workspace at
plans/agent-platform/2026-09-07-run-ledger-design.md. This copy exists because a build
seat works in a disposable worktree of THIS repository and cannot read that path — a
task told to "read the spec" there produces an empty patch, which is how
tools-run-ledger-5 failed. The contract belongs in the repository it governs.

If the two disagree, this file governs the code and the workspace copy governs the
programme. Keep them in step. -->

# Run stats store (P0) — design

Date: 2026-09-07. Status: approved in chat, spec for review. Author: chair session with Pat.

The stats store is the substrate for routing work by evidence instead of by hand. Today the
provider profile's routing decisions are recorded as PROSE in
`coxswain-cartridges/providers/claude-code.yaml` — "Measured over 2026-09-05/06, about 75
runs: the arbiter was right nearly every time it overturned a review". That comment is a
human reading run records and editing YAML. **This spec is the tool that comment describes.**

P0 ends when the questions are answerable. It does not answer them, and it changes no
routing.

## 0a. Naming

**"Ledger" is taken.** `~/.local/state/agent-graphs/ledger.jsonl` is the autonomy ledger
and has been since before this work. This store is therefore the **stats store**,
`workspace/stats/stats.db`, reached through `cox stats`. The two are related — the
autonomy ledger is one of the stats store's INPUTS (§1) — and must never share a name.

## 0. Program context

The stats store is the first of six sub-projects. It is first because P4 and P5 cannot begin
without it.

| | Sub-project | Depends on |
|---|---|---|
| **P0** | **Run stats store** — durable store, outcome join, `cox stats` | — |
| P1 | Vendor axis — runner interface + capability declaration | — |
| P2 | home/ctop split — home pure dashboard, `ctop` process view | — |
| P3 | Command table — one table generates CLI + slash surface | — |
| P4 | Router + steward — stats-driven selection, evidence-backed profile proposals | P0 |
| P5 | Courier — cross-session references bus | P0 |

P1, P2 and P3 are independent of P0 and of each other.

## 1. What exists today

Measured on `workspace/runs`, 2026-09-07:

- **247** `<run>.usage.json` — `summary` plus `calls[]`, one entry per model invocation
  carrying `role, tier, model, cost_usd, turns, duration_ms, input_tokens,
  cache_read_tokens, cache_creation_tokens, output_tokens, tools, trace`.
- **237** `<run>/tasks/<phase>/<ticket>.json` — `run_id` (`<run>:<phase>:<ticket>`),
  `ticket, scope, plan, build, review, adversary, arbitration, fix_loop, change_facts,
  proposals, plan_competition, plan_attack, plan_gate`.
- `<run>:<node>.json` — `cartridge_sha, cartridge_team, principal, gate_diffs[]`
  (each with `kind, decision, outcome, risk, target, applied, edited`), `human_minutes`.
- `<run>.log` — parsed today by `agent_tools/events.py` regexes into verdicts,
  quarantines, budget stops, leader transitions.
- `<run>.launched.json` — `at`, `launched_by`. **No host field.**
- `<run>-trace/<role>-<n>.jsonl` — full per-call trace; `.error.jsonl` on failure.
- **`~/.local/state/agent-graphs/ledger.jsonl` — 397 rows, the AUTONOMY LEDGER.**
  One row per gate decision: `run_id` in `<run>:<node>` form, `kind` (`item_create`,
  `state_move`, `draft_pr_create`, `self_modification`, `merge_stack`, `stack_rebase`),
  `outcome`, `risk`, `principal`, `provider_profile`, `cartridge_sha`, `ts`, and
  `attempts` on 28 rows.

Four facts that shape the design:

1. **194 of 247 runs repeat a role within the run** (fix-loop retries, and per-item arms —
   one run shows `work_item_arm: 12`). A table keyed by (run, role) would collapse real
   attempts. Attempt number is load-bearing.
2. **`calls[]` entries carry no task id.** Role, tier and model are recorded; the ticket
   the call was working on is not. The calls-to-tasks join must be reconstructed.
3. **`landed` is an explicit field in only 6 of 237 task records.** Outcome is otherwise
   implicit in log lines and `gate_diffs[].outcome`.
4. **The autonomy ledger is a fifth source, and it lives OUTSIDE `workspace/runs`.**
   It therefore survives `cox runs clean`, it is machine-local and unsynced, and it
   already carries `outcome` and `provider_profile` per `<run>:<node>` — the closest
   thing to a written-down outcome the platform has. The spike of §2 must read it
   before concluding the join is absent.
5. **A BUDGET-STOPPED RUN WRITES NO `usage.json` AT ALL.** Measured 2026-09-07: two
   decompose runs died on `error_max_budget_usd` having spent $0.4454 and $0.3936, and
   neither produced a usage record. That $0.84 is invisible to every aggregate built on
   `*.usage.json` — it survives only in the trace's final result line
   (`total_cost_usd`, `subtype: error_max_budget_usd`). The ingester MUST read trace
   result lines, not just usage records, or the stats store will systematically
   under-report cost and — far worse — will be blind to exactly the runs that failed.
   A store that sees only successes cannot answer a question about reliability.
6. **No run record names its host.** A second machine with a different provider profile
   (the planned local-8B on the TensorBook) would be indistinguishable in the data.

## 2. The risk, and the spike that retires it

Facts 2 and 3 together mean the headline query — landed rate per (role, model) — is not
computable from historical data without reconstructing a join that was never written down.
This is the single thing most likely to sink P0 quietly, by producing a populated database
whose central number is wrong.

**The first task of implementation is a timeboxed spike**, before any schema is committed:
determine whether `calls[]` entries can be attributed to a ticket, by

- trace-file ordering (`<role>-<n>.jsonl` against the Nth call of that role),
- the sub-objects inside the task record (`plan`, `build`, `review`, `arbitration`) —
  whether any names a call, a trace, or a cost, and
- **`~/.local/state/agent-graphs/ledger.jsonl`**, whose `run_id` is already `<run>:<node>`
  and which carries `outcome` and `attempts` directly. This is the most promising of the
  three and must be checked first.

The spike reports one of: exact join available; heuristic join with a measurable error
rate; or no join.

**Fallback, if the join is heuristic or absent.** Ingest both grains anyway and store
`join_confidence` per call row. Degrade to run-grain attribution for the 53 runs that
repeat no role, which give a smaller but sound sample. Report coverage in every aggregate:
a number computed from 53 runs must never be displayed as though it came from 247.

No schema is frozen before the spike reports.

### 2a. THE SPIKE HAS REPORTED (2026-09-07)

Run by the chair session directly, not by the harness — see the note at the end of this
section. Verdict: **heuristic join, error rate 7.1% — but the errors are not random.**

**Route 3 (task-record sub-objects) is dead.** `plan`, `build`, `review`, `arbitration`,
`fix_loop` and `handoff` carry no call id, no trace path and no cost. `build` holds
`commands_run, files_touched, patch, summary` and nothing that names the call that
produced it.

**Route 1 (the autonomy ledger) does not close it alone.** `ledger.jsonl` rows key on
`<run>:<node>` and carry no `target`. The ticket appears instead in `<run>:<node>.json`
under `gate_diffs[].target` — so a NODE maps to a TICKET there, but a run with several
tickets has several gate_diffs against one node and nothing distinguishes which call
produced which.

**Route 2 (ordering) works, via a counting identity.** For each run,
`sum(fix_loop.attempts)` over its task records equals the number of `build` calls in
`<run>.usage.json` in **171 of 184** runs that have both — 92.9%. Where the identity
holds, build calls assign to tickets in order (ticket order x attempts).

**The 7.1% is concentrated, not scattered.** Of ten sampled mismatching runs, **ten**
show a quarantine or a budget stop in their log. The join does not degrade gracefully
across the corpus; it is close to exact on clean runs and close to useless on failed
ones. Reporting this as "92.9% accurate" would be true and misleading: the runs it
cannot join are precisely the population a reliability question is about, which is the
same hazard as fact 5 above.

CONSEQUENCES FOR THE SCHEMA. `join_confidence` is not optional and not decorative: it
must be written per call row, and every aggregate over outcomes must be able to exclude
or flag low-confidence rows. An analysis that silently drops unjoinable calls will report
that everything succeeds. Ordering is inferred from a counting identity, not read from a
recorded id, so the ingester must verify the identity per run and refuse to assign when
it fails rather than assigning anyway.

MEASURED, correcting §2's estimate: **54** of 250 runs repeat no role (the spec
estimated 53). That is the sound run-grain fallback population.

WHY THE CHAIR RAN THIS AND NOT THE HARNESS. `tools-run-ledger-4` quarantined this task
with "unreachable corpus". The build seat works in a disposable worktree of the target
repository, and the corpus lives in `workspace/runs` and `~/.local/state/agent-graphs/`
— outside it. A spike is a question about data, not a change to a repository, and it was
mis-routed by being bundled into the epic. The platform has no seat for analysis over
the workspace corpus; that gap is filed separately.

## 3. Schema

Three tables. SQLite, at `workspace/stats/stats.db`.

**This schema is proposed, not frozen.** It is what the spike of §2 is testing against:
if the calls-to-tasks join proves absent, `calls.task_id` and `calls.join_confidence`
change meaning or disappear, and the aggregates of §5 are defined at run grain instead.
Everything else below is independent of the spike's outcome.

**`runs`** — one row per run.
`run_id` PK, `started_at`, `ended_at`, `cartridge_sha`, `cartridge_team`, `provider_profile`,
`vendor`, `host`, `launched_by`, `principal`, `human_minutes`, `schema_version`.

`host` is recorded from ingest time (the running machine) for backfilled runs and marked
`inferred`; from the record itself once P1 writes one. `vendor` likewise defaults to
`claude-code` for all 247 historical runs.

**`calls`** — one row per model invocation.
`run_id` FK, `seq`, `role`, `attempt`, `tier`, `model`, `cost_usd`, `turns`, `duration_ms`,
`input_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `output_tokens`, `tools`,
`trace_path`, `failure_class`, `challenger`, `task_id`, `join_confidence`,
`recovered_from_trace`.

- `attempt` is the ordinal of this call among calls of the same role in the same run. It is
  computed at ingest, not read.
- `failure_class` is a small closed enum extracted ONCE at ingest from the trace and the
  log — `budget_stop`, `tool_error`, `empty_patch`, `refused`, `ok`, `unknown`. The
  taxonomy exists so that **no agent ever reads a raw trace to answer a routing question.**
  That is where P0's token saving actually comes from.
- `trace_path` is a pointer. Trace CONTENT is never copied into the stats store.
- `challenger` marks a deliberate exploration call (see §6). Default false.
- `recovered_from_trace` marks a row built from a trace's terminal `type: result` line
  because the run wrote no usage record at all (fact 5), rather than from a usage
  record's `calls[]` entry. Default false; token counts, `duration_ms`, `tier` and
  `model` are unrecoverable from that line and stay `NULL` on a recovered row.

**`tasks`** — one row per task attempt.
`run_id` FK, `task_id`, `ticket`, `phase`, `initiative`, `attempt`, `outcome`,
`review_rounds`, `arbitration_verdict`, `fix_loop_rounds`, `cost_usd`, `reason`.

`outcome` is a closed enum: `landed`, `quarantined`, `budget_stop`, `skipped`, `unknown`.
Derivation order is explicit and recorded per row in `outcome_source`: the explicit
`landed` field where present (6 rows), then `gate_diffs[].outcome`, then the log line.
`unknown` is a real and acceptable value — an ingester that guesses is worse than one
that abstains.

### Why three tables and not one

Routing decisions are per-call (role x tier x model). Outcomes are known per-task. One
task consumes many calls across a fix loop. Flattening to per-call loses the outcome;
flattening to per-run loses model attribution. The grain mismatch is the whole reason the
question cannot be answered today.

### The metric that matters

**Attempts-to-land, not cost.** Cost is trivially measurable and will otherwise become the
target. A build landing first try at $0.50 beats one landing on the third attempt at $0.30
each. `cox runs series` already exposes `cost_per_landed` at run grain; the ledger's
purpose is to expose it per (role, model, tier).

## 4. Ingest

`cox stats ingest [RUNS_DIR]`

- Idempotent upsert keyed by `run_id`. Re-running over the same directory is a no-op.
- **Re-ingestable by design.** The first schema will be wrong. Because the ledger is
  derived rather than written live, the schema can be changed and the whole corpus
  re-ingested. This property is the reason the ingester approach was chosen over
  direct-write from `coxswain-graphs`, and it must not be traded away later without
  a deliberate decision.
- Touches `coxswain-tools` only. `coxswain-graphs` is unchanged by P0.
- **Unparsed input is reported, never swallowed.** Exit non-zero with a count and a sample
  when lines or files fail to parse. A silent ingester is how a ledger becomes confidently
  wrong.
- Becomes the documented precondition for `cox runs clean`, which today can delete the
  only copy of the evidence.

### Historical log vocabulary is a permanent input format

`events.py` parses log TEXT, not structured records:

    ^leader taken: (\S+) \(pid (\d+)\) on (\S+)$

The 247 existing logs are immutable evidence. Any future change to emitted log strings —
including the proposed `leader` to `chair` rename, which is NOT approved and is out of
scope here — must leave these parsers able to read both spellings permanently. The
ingester is written against this constraint from the start rather than retrofitted to it.

## 5. Query surface

`cox stats roles` — landed rate, attempts-to-land, and $/landed per (role, model), with
sample size and coverage on every row.
`cox stats explain <role>` — the failure-class breakdown behind that row.
`cox stats series` — the existing per-run series, read from the ledger.
`--json` on all of them.

**Default output is hard-capped at a few hundred tokens.** Aggregates only; drill-down on
request. If `cox stats` becomes something every session reads at startup, it lands in every
context window, and the tool built to remove token cost becomes one. The cap is a tested
requirement, not a style preference.

## 6. Provision for P4, built now, unused now

`challenger` on `calls` exists in P0 so that P4's exploration mechanism has somewhere to
land. The reasoning belongs here because it constrains the schema:

Records of runs where sonnet did the build can never show that haiku would have sufficed.
Without deliberate exploration, stats-driven routing only ever confirms what was already
chosen. P4's mechanism — one in N low-risk nodes run one tier below default, never on the
critical path — requires that such a call be **distinguishable from a real regression**.
Untagged, the first exploration failure reads as a regression and exploration is switched
off. The column is one boolean now; retrofitting it later means the exploration data
gathered before the retrofit is unusable.

## 7. Out of scope for P0

No router. No steward. No profile edits. No vendor changes. No direct-write from
`coxswain-graphs`. No rename of the leader lock.

**Two known blind spots, recorded rather than solved:**

1. **Interactive sessions write no usage record.** Bare `cox` and `t` from home burn the
   same subscription as the headless nodes and produce no `usage.json`. A token-burn
   program built only on headless-node data optimizes the measured slice while the larger
   unmeasured one drifts. Closing this belongs with P1, which owns the interactive
   launch path.
2. **`cox route leader` cannot act on a stale lock.** `status` computes `stale`; no
   subcommand clears it. `release` checks the calling pid, and `take --steal` binds the
   lock to the invoking process — so a lock taken from a short-lived `cox` subprocess is
   dead on return. On 2026-09-07 this required deleting `runs/leader.json` by hand. The
   fix is a `clear` subcommand. It was scoped into P0 phase 1 and has MOVED to the
   chair-rename work (approved 2026-09-07), which renames this whole command group and
   would otherwise collide with it on the same subcommand; it ships there as `cox route
   chair clear`. That work is sequenced BEFORE this epic for the same reason, and it
   lands the permanent dual-vocabulary log parsers §4 depends on. The general pattern —
   a coordination file that can be diagnosed as stale but not remediated — must be
   designed out of P5's courier.

## 8. Testing

- Pure functions over literals for every derivation: `attempt` numbering, `outcome`
  resolution order, `failure_class` extraction, the token cap on rendered output.
- Fixture corpus of real run records, including the pathological shapes actually present:
  `work_item_arm: 12`, a run with `.error.jsonl` traces, a task record with an explicit
  `landed`, and one without.
- Idempotency: ingest twice, assert identical table contents.
- Re-ingest: change schema version, re-ingest, assert no data loss.
- Coverage reporting: assert an aggregate computed from a partial join reports its
  coverage rather than presenting itself as complete.
