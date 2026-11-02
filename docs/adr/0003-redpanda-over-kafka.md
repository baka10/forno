# ADR-0003: Redpanda in place of Kafka

**Status:** Accepted · **Date:** 2026-01-15

## Context

The system needs a partitioned, durable log with consumer groups. It also needs to start from cold
in under sixty seconds on a laptop and in CI, because a demo nobody runs proves nothing.

Apache Kafka in Docker means a broker plus (depending on version) Zookeeper or KRaft configuration,
a JVM, and a slow, memory-hungry startup that dominates integration test time.

## Decision

**Redpanda**, which implements the Kafka protocol in a single native binary with no JVM and no
Zookeeper. Client code is `aiokafka` and unchanged.

## Consequences

- Cold start is a few seconds rather than tens; the CI integration job is roughly a third of the
  runtime, and `make up` fits inside its sixty-second budget.
- `rpk` is a materially better operational CLI, which makes the runbook commands short enough that
  people actually run them.
- Every concept transfers exactly: topics, partitions, keys, consumer groups, offsets, compaction,
  DLQ. Nothing here is Redpanda-specific.
- Cost: not Kafka. A team standardised on MSK or Confluent runs Kafka.

## Alternatives rejected

**Apache Kafka.** The default answer, and the right one in production if that is what the
organisation runs. Rejected for the development and CI story only.

**NATS JetStream.** Lighter still and a fine broker, but a different model, and the transferable
knowledge is narrower.

**Postgres as the queue** (`SKIP LOCKED`, no broker at all). Genuinely tempting — it would remove a
container and still support the outbox. Rejected because fan-out to multiple independent consumer
groups with independent offsets is precisely what a log gives you and a queue does not, and the
kitchen projector, the stats projector and the SSE fan-out are three such consumers with different
delivery semantics. That requirement is what justifies a broker; without it, there would be no case
for one.

## What would change this

Deploying to an environment with managed Kafka. The change is a bootstrap URL. That it is only a
bootstrap URL is the reason this decision is low-risk.
