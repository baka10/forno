# Architecture Decision Records

Every non-obvious decision in this project, written in the same shape: the forces, the choice, the
alternatives that were rejected and why, and — the part most ADR collections omit — **the specific
signal that would change the answer.**

A decision without a stated reversal condition is a preference. A decision with one is engineering.

| # | Decision | Status |
|---|---|---|
| [0001](0001-postgres-system-of-record.md) | Postgres is the system of record; no event sourcing | Accepted |
| [0002](0002-transactional-outbox.md) | Hand-written transactional outbox, not Debezium | Accepted |
| [0003](0003-redpanda-over-kafka.md) | Redpanda in place of Kafka | Accepted |
| [0004](0004-sse-over-websockets.md) | SSE, not WebSockets | Accepted |
| [0005](0005-layered-architecture.md) | Global layers, not package-by-feature | Accepted |
| [0006](0006-kitchen-tickets-as-projection.md) | Kitchen tickets are a projection; commands route through the order | Accepted |
| [0007](0007-cp-sat-promise-estimator-seam.md) | CP-SAT promise estimator specified, not implemented | Proposed |
| [0008](0008-simulator-as-external-client.md) | The simulator is an external client | Accepted |
| [0009](0009-scaled-domain-time.md) | Domain time is scaled; wall-clock time is not | Accepted |
| [0010](0010-abstraction-budget.md) | An abstraction needs two implementations or a documented seam | Accepted |
| [0011](0011-mcp-server-as-external-operator-client.md) | The MCP server is an external operator client | Accepted |

Format: [Michael Nygard's](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions),
lightly extended with a "what would change this" section.
