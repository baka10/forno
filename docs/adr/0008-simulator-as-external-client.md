# ADR-0008: The simulator is an external client

**Status:** Accepted · **Date:** 2026-01-24

## Context

The project needs to demonstrate itself: one command, a working pizzeria, populated dashboards. The
simulator could reasonably import the application's use cases and drive them directly — faster, less
code, no HTTP overhead.

## Decision

**The simulator lives in a separate top-level package and communicates only over the public HTTP API
and SSE streams.** No imports from `src/forno`, no database access, no shared process. Enforced:

```ini
[importlinter:contract:simulator-is-external]
type = forbidden
source_modules = simulator
forbidden_modules = forno
```

## Consequences

One constraint turns one piece of code into four:

1. **A demo.** `make demo` produces a full service with real data in the dashboards.
2. **A load generator** driving real traffic through the public API.
3. **Proof that the API is sufficient for a frontend that was never written.** This is the answer to
   the obvious objection to a backend-only repository. The waiter actor consumes SSE, renders
   nothing, and reacts correctly — if it can do every role's job through the public contract, the
   contract is complete.
4. **A system-level property test.** The shadow ledger is built only from observable output, and
   asserts a deliberately reduced set of invariants (four, currently — see
   [Simulator](../simulator.md) "The shadow ledger" for which and why) over the real transport
   under real concurrency. It runs as the top CI gate.

The feedback loop is the underrated part: **every time the simulator wanted a shortcut, that was a
real gap in the API, and it got fixed in the API.** An in-process harness would have papered over
each one. The `409`-on-late-cancel path exists because an external cashier had to be told no.

Cost: slower than direct calls, and the actors must handle real HTTP failure modes — which is the
point, not overhead.

## Alternatives rejected

**In-process harness.** Faster, and proves nothing. A program calling its own functions in a loop
demonstrates that the functions are callable.

**Database fixtures for demo data.** A second write path that drifts from the real one, and it
bypasses exactly the validation the demo should be exercising. See
[configuration.md](../configuration.md) — the manager actor configures the pizzeria through the
management API for the same reason.

**LLM-driven customers.** Fun, and destroys reproducibility, requires keys in CI, and makes every
run non-reproducible. Available as an opt-in profile that nothing depends on.

## What would change this

Nothing. This is load-bearing: relaxing it silently converts the strongest evidence in the
repository into a demo script.
