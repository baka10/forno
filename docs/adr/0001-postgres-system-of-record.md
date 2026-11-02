# ADR-0001: Postgres is the system of record; no event sourcing

**Status:** Accepted · **Date:** 2026-01-12

## Context

The system is event-driven, and events are the mechanism by which the kitchen display, the
statistics projector and connected clients learn about state changes. The obvious next step —
"events are the source of truth, state is derived" — is event sourcing.

An order has money on it, a legally meaningful receipt, and a state machine that a restaurant
manager needs to be able to inspect and correct.

## Decision

**PostgreSQL holds current state as ordinary tables. Kafka carries propagation only.** The event log
is not authoritative and can be truncated without data loss.

## Consequences

- Correctness is verifiable by reading a table. `SELECT * FROM orders WHERE id = …` answers "what is
  true right now" with no replay and no projection.
- Constraints, foreign keys and transactions do the work they are good at.
- **The broker being down is not an outage.** Orders are still accepted, priced and durable; the
  kitchen display lags until the outbox drains.
- Cost: no free audit log, no free temporal queries. Where auditability genuinely matters — money —
  it is bought explicitly with an append-only `order_events` table.
- Cost: a database commit and a broker publish are not atomic, which is a real problem and the
  subject of [ADR-0002](0002-transactional-outbox.md).

## Alternatives rejected

**Full event sourcing.** Genuinely better for audit and temporal queries. Rejected because it moves
every correctness question behind a projection, makes "what is the current price of this order" a
fold rather than a read, and — decisively for a repository meant to be *read* — a reviewer cannot
verify the model without running it. The complexity is real and the payoff here is not.

**CQRS with a separate read database.** Adds an inter-store consistency problem to solve a scaling
problem this system does not have. Read models live in the same Postgres instance, in separate
tables.

## What would change this

Regulatory requirement for a complete, tamper-evident history; or a genuine need to reconstruct
state at an arbitrary past instant. Both would justify event sourcing the `Order` aggregate
specifically, leaving the rest as-is.
