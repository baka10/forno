# ADR-0006: Kitchen tickets are a projection; chef commands route through the order

**Status:** Accepted · **Date:** 2026-01-20

## Context

The kitchen needs its own view: items grouped by station, sorted by urgency, with oven occupancy.
Two options.

**A — Tickets own their state.** The kitchen consumer maintains a second write model. Chef actions
write to it directly, and it emits events back. Natural to write, and a chef command is one local
transaction.

**B — Tickets are a pure projection.** Read-only, derived from order events. Chef actions are
commands against the *order* aggregate; the projection updates when the resulting event arrives.

Option A creates two sources of truth for the same fact. "Is item 4b2f in the oven?" gets two
answers that can disagree, and reconciling them requires inventing a conflict-resolution policy for
a question that has exactly one physical answer.

## Decision

**Option B.** `kitchen_tickets` and `ticket_lines` are a read model. Chef commands are addressed
against the ticket for client convenience (`POST /kitchen/tickets/{tid}/items/{iid}/oven-in`) and
routed internally to the order aggregate, where the invariants live.

## Consequences

- One write model, one place where FSM rules are enforced. Oven-slot capacity, cancellation
  legality and fire-together evaluation are checked once, not twice.
- **The kitchen projector is stateless and replayable.** Deleting the read model and resetting the
  consumer group rebuilds it exactly — which is what makes `make chaos` a demonstration rather than
  a gamble, and what makes simulator invariant #3 checkable.
- Cost: a chef command is not instantly reflected in `GET /kitchen/tickets`; it takes an outbox poll
  plus a consume, typically well under a second. The SSE stream carries the state change immediately,
  so the client sees it before the read model catches up.
- Cost: chef commands are indirect, which reads as odd until you try to write the reconciliation
  logic that option A requires.

## Alternatives rejected

**Option A.** Faster reads, local writes, and two sources of truth. Rejected on the reconciliation
cost, which is unbounded.

**Serving the kitchen view from the order tables directly.** No projection at all. Rejected because
the station-oriented, urgency-sorted view is a genuinely different shape, and computing it per
request during the rush is the query that falls over exactly when it matters.

## What would change this

If the kitchen ever needed to operate while disconnected from the ordering system — a local-first
KDS surviving a network partition — it would need to own its state, and this decision would invert.
