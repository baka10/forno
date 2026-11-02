# ADR-0002: Hand-written transactional outbox, not Debezium

**Status:** Accepted · **Date:** 2026-01-14

## Context

Given [ADR-0001](0001-postgres-system-of-record.md), each state change must both persist and be
announced. These two writes cannot be made atomic:

- Publish first, commit second → an announced order that does not exist.
- Commit first, publish second → a persisted order nobody hears about.

This dual-write hazard is the defect most "I used Kafka" systems ship without noticing, usually
because the failure only appears under a crash between the two operations.

## Decision

Every state change writes an `outbox` row **in the same transaction as the aggregate**. A separate
`outbox-relay` process polls, publishes and marks rows sent. The relay is about sixty lines,
written by hand.

```sql
SELECT * FROM outbox WHERE sent_at IS NULL
ORDER BY id FOR UPDATE SKIP LOCKED LIMIT :batch;
```

`FOR UPDATE SKIP LOCKED` makes the relay horizontally scalable with no coordination: multiple
replicas claim disjoint batches without blocking each other.

## Consequences

- Publication becomes at-least-once, so **every consumer needs an inbox**
  (`processed_events(event_id, consumer)` written in the same transaction as its effect). At-least-once
  delivery plus idempotent application is effectively-once *effects*, which is the only kind of
  exactly-once that exists.
- Publication lag is a visible, alertable metric (`forno_outbox_lag_seconds`) rather than an
  invisible correctness hazard.
- Ordering is preserved per aggregate by publishing in `id` order and keying by `order_id`.
- Cost: one extra process, and roughly 200ms of publication latency at the default poll interval.

## Alternatives rejected

**Debezium / CDC.** Strictly more capable and requires no application code, but it needs Kafka
Connect, a replication slot, and a schema-drift story. For a repository whose value is being
readable, a black box that reviewers cannot inspect is a poor trade — and reimplementing the pattern
is the more instructive artefact.

**Publish inside the transaction.** Not atomic; a broker timeout after the commit still loses the
event, and a broker timeout before it holds a database transaction open on network I/O.

**Two-phase commit.** Kafka does not participate in XA, and 2PC would trade an availability problem
for a worse one.

## What would change this

Migrate to Debezium when the relay's single-replica throughput becomes the bottleneck **and** relay
replicas are already scaled out, or when a second service needs to consume Postgres changes this
application does not model. (The ceiling is not yet measured — `forno_outbox_published_total` and
`forno_relay_batch_seconds` are the metrics that would establish it under load.) The `EventPublisher`
port makes that a contained change: the outbox table becomes the CDC source and the relay is deleted.
