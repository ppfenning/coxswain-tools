# Spike: the calls-to-ticket join

Run 2026-09-07 over the live corpus (250 `*.usage.json`, 237 task records, 397 autonomy
ledger rows). Recorded here because the build seat cannot reach that corpus from a
worktree; see "Why this file exists" below.

## Verdict

**Heuristic join, error rate 7.1% — and the errors are not random.**

## What was tried

**Task-record sub-objects — dead.** `plan`, `build`, `review`, `arbitration`, `fix_loop`
and `handoff` carry no call id, no trace path and no cost. `build` holds
`commands_run`, `files_touched`, `patch`, `summary`, and nothing naming the call that
produced it.

**The autonomy ledger — insufficient alone.** `~/.local/state/agent-graphs/ledger.jsonl`
keys rows on `<run>:<node>` and carries no `target`. The ticket appears instead in
`<run>:<node>.json` under `gate_diffs[].target`, so a NODE maps to a TICKET — but a run
with several tickets has several gate_diffs against one node, and nothing says which
call produced which.

**Ordering, via a counting identity — works.** For each run, `sum(fix_loop.attempts)`
over its task records equals the count of `build` calls in `<run>.usage.json` in
**171 of 184** runs that have both: 92.9%. Where the identity holds, build calls assign
to tickets in order (ticket order x attempts).

## The 7.1% is concentrated, not scattered

Of ten sampled mismatching runs, **ten** show a quarantine or a budget stop in their log.
The join is close to exact on clean runs and close to useless on failed ones. Reporting
it as "92.9% accurate" would be true and misleading: the runs it cannot join are exactly
the population a reliability question is about.

## What this forces on the implementation

- `join_confidence` is load-bearing, written per call row. Every aggregate over outcomes
  must be able to exclude or flag low-confidence rows. An analysis that silently drops
  unjoinable calls will report that everything succeeds.
- Ordering is INFERRED from a counting identity, never read from a recorded id. The
  ingester must verify the identity per run and **refuse to assign when it fails**,
  rather than assigning anyway.
- Run-grain fallback population, measured: **54** of 250 runs repeat no role.

## Why this file exists

`tools-run-ledger-4` quarantined the spike task with "unreachable corpus". The build seat
works in a disposable worktree of this repository; the evidence lives in
`workspace/runs` and `~/.local/state/agent-graphs/`, outside it. The spike was therefore
run by the chair session directly and its finding committed here, at the surface the task
declared, so that later seats can read it.
