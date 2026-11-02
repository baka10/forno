# ADR-0009: Domain time is scaled; wall-clock time is not

**Status:** Accepted · **Date:** 2026-01-25

## Context

A pizza takes eight minutes and a dinner service takes two hours. Nobody watches a two-hour demo.
Compression is required — the question is what gets compressed.

The tempting approach is to accelerate the system's clock and let everything follow.

## Decision

**A `time_scale` factor applied to domain durations only.** Bake time 480 domain seconds at
`time_scale: 120` is 4 wall-clock seconds. The wall clock, and everything measured against it, is
untouched.

| | scaled | untouched |
|---|---|---|
| Bake, prep, walk, deliberation durations | ✓ | |
| Walkout and promise thresholds | ✓ | |
| Prometheus scrape interval | | ✓ |
| `rate()` and burn-rate windows | | ✓ |
| Log and event timestamps | | ✓ |

## Consequences

- **The observability stack stays honest.** Accelerating wall-clock time would make every `rate()`,
  every histogram and every burn-rate calculation in Grafana a fiction — which would quietly destroy
  the most valuable part of the project in order to speed up the second most valuable part.
- A two-hour service completes in about a minute, and the alerts fire on real timescales.
- This is the payoff on the `Clock` port. Scaling lives in one injected `ScaledClock` rather than in
  `sleep()` calls scattered across six actors, and `FrozenClock` makes promise-breach logic
  unit-testable.
- **The API has to know the factor too.** Promises and oven-slot release times are computed by the
  server from domain durations (bake seconds on the menu) but stored as wall-clock instants, so the
  server's `Clock` carries the same `time_scale` (`FORNO_TIME_SCALE`, set by `make demo`/`make live`
  from the scenario file). `/health/ready` reports it and the simulator refuses to run against a
  mismatch — at 120x a promise made in wall seconds would be 120 times too generous and no order
  could ever be late, which silently turns the whole SLO story into fiction.
- Cost: latency histograms are in **scaled domain seconds**. The README, the dashboards and the
  simulation report all say so. Being explicit about the distortion reads better than pretending it
  is not there.

## Alternatives rejected

**Accelerate wall-clock time** (libfaketime or similar). Everything follows automatically — and
every rate-based metric and alert becomes meaningless. Rejected outright.

**Shorten the durations in the scenario file.** A 30-second bake time is not a scaled pizzeria, it is
a different one: queueing behaviour, oven contention and fire-group spread all change shape.
`time_scale` preserves the *ratios*, which is what makes the simulation informative.

**No compression.** Honest, and nobody runs a two-hour demo.

## What would change this

Running the simulator against a production-like environment for capacity planning would call for
`time_scale: 1` — which is already supported, and is the default in the `load` profile.
