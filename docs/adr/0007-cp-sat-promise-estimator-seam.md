# ADR-0007: CP-SAT promise estimator — specified, not implemented

**Status:** Proposed · **Date:** 2026-01-22

## Context

`promised_at` anchors the SLO. The current `NaiveEstimator` divides remaining bake work by free oven
slots and adds a fixed margin. It ignores `fire_group`, prep-station capacity, remake priority and
per-slot release times — all of which materially change when an order can realistically be ready.

Underlying this is a genuine combinatorial scheduling problem: assign items to oven slots and prep
stations over time, subject to capacity and coursing constraints, minimising promise breach.

## Decision

**Ship the seam, not the solver.** A finished repository with one visible, fully specified extension
point is worth more than an unfinished ambitious one.

```python
class PromiseEstimator(Protocol):
    def promise(self, order: Order, state: KitchenState) -> PromisedTime: ...
```

Selected by name at startup (`FORNO_PROMISE_ESTIMATOR`). `KitchenState` **already carries everything
a solver needs** — per-slot occupancy with release times, per-item prep and bake durations, station
capacities, fire-together groupings and priorities — even though the naive implementation reads
three fields. Shaping the input correctly is the part that makes the seam credible; declaring a
Protocol is the easy half.

## The model, in full

**Decision variables.** For each item *i*: `start_prep[i]`, `start_bake[i]`, integer seconds from
now; `slot[i] ∈ ovens × slots`, a boolean assignment.

**Constraints.**
- *Precedence:* `start_bake[i] ≥ start_prep[i] + prep_seconds[i]`
- *Prep capacity:* cumulative constraint over prep stations, demand 1, capacity = station count
- *Oven capacity:* no-overlap per slot, interval `[start_bake, start_bake + bake_seconds]`
- *Slot availability:* `start_bake[i] ≥ release_time[slot[i]]`
- *Fire-together:* for items in group *g*, `max(ready) − min(ready) ≤ window` (default 90s)
- *Priority:* remakes precede non-remakes in the same group

**Objective.** Lexicographic: minimise total promise breach across in-flight orders, then minimise
fire-group spread, then minimise makespan.

**Time budget.** 150ms hard limit on the solve. On timeout, return the best feasible solution found;
if none, fall back to `NaiveEstimator`. **The promise path must never block order acceptance** — a
scheduler that makes the till slow is worse than a scheduler that guesses.

**Determinism.** Fixed worker count and fixed random seed, so identical input yields an identical
promise. Without that, the comparison is not an experiment.

## Consequences

- A second estimator is a pull request, not a rewrite: register it under `FORNO_PROMISE_ESTIMATOR`
  and, because the simulator is deterministic, compare it against `NaiveEstimator` on byte-for-byte
  identical traffic.
- `forno_fire_group_spread_seconds` exists specifically to measure the improvement. The metric
  precedes the feature on purpose.
- Cost: `ortools` is a heavy dependency and stays optional, behind an extras group.

## What would change this

Two things would move this from Proposed to Accepted: the naive estimator's breach rate exceeding
the error budget under `busy-friday` (it currently sits near the top of the band), or a decision to
demonstrate the optimisation capability explicitly. Both are the same day's work, and neither
touches anything outside `adapters/scheduling/`.
