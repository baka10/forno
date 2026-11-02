# ADR-0010: An abstraction needs two implementations or a documented seam

**Status:** Accepted · **Date:** 2026-01-26

## Context

Hexagonal architecture in Python has a characteristic failure mode: Java cosplay. A DI container, an
`IOrderServiceFactory`, a `Repository[T]` generic, and a Protocol for every class — each with exactly
one implementation, none of which will ever have a second. Reviewers read that as inexperience, not
rigour, and it is *more* expensive to change than the concrete code it replaced.

The opposite failure — no seams at all — makes the interesting parts untestable and unextendable.

## Decision

**An abstraction earns its place only if it has two real implementations today, or it is a seam
documented in an ADR.** Everything else is a concrete class.

### Ports that exist

| Port | Implementations | Justification |
|---|---|---|
| `PromiseEstimator` | `NaiveEstimator` + documented CP-SAT seam ([0007](0007-cp-sat-promise-estimator-seam.md)) | The extension point the design is aimed at |
| `EventPublisher` | `OutboxPublisher`, `InMemoryPublisher` | Two real ones; keeps application tests container-free |
| `Clock` | `SystemClock`, `ScaledClock`, `FrozenClock` | Three. Promise-breach logic is untestable against `datetime.now()` |
| `OrderRepository` | one | Justified **not** as database swappability — that is a fiction nobody believes — but as the single guarded write path enforcing FSM invariants |

### Rejected, deliberately

**DI container.** FastAPI's `Depends` is one, and it is already there.

**Generic `Repository[T]`.** Every aggregate has different invariants; a generic CRUD interface
either leaks them or hides them. `OrderRepository` has the methods the order needs and nothing else.

**Separate read database.** Solves a scaling problem this system does not have and adds an
inter-store consistency problem it would then have.

**A Protocol per adapter.** `SQLAlchemyOrderRepository` will not be swapped for MongoDB. Pretending
otherwise costs indirection and buys nothing.

## Consequences

- The call graph is short. Reading a use case does not require chasing three interfaces to find the
  code that runs.
- Every remaining Protocol has a stated reason, which means a reviewer can evaluate the judgement
  rather than the pattern vocabulary.
- Cost: adding a genuinely new adapter later means extracting an interface at that point — cheap,
  mechanical, and done with the requirement in hand rather than imagined.

## On naming

Classes are named for what they do — `KitchenTicketProjector`, `OutboxRelay`, `NaiveEstimator` — and
the pattern is named once, in the ADR. Pattern-named classes read as astronautics; pattern-named
*decisions* read as literacy.

## What would change this

A second real backend for any port. That is the trigger, and it is a low bar by design — the rule is
"two implementations", not "never abstract".
