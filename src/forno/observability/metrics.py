"""The metric catalogue from docs/observability.md, declared once here.

Bucket choices matter: default histogram buckets stop at ten seconds, useless
for something that takes eight minutes (docs/observability.md "Service level").
Cardinality rules (docs/observability.md "Cardinality"): never `order_id`,
`table_number`, `ticket_id`, `correlation_id`, raw path, or error message as a
label. `menu_item` is the only label that grows with data, bounded by menu size.

Populating these (`.observe()` / `.inc()` / `.set()`) happens at the call
sites in `application/` and `adapters/`; this module only declares shape.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

_READY_BUCKETS = (60, 120, 300, 480, 600, 900, 1200, 1800)

# --- Service level -------------------------------------------------------

order_ready_seconds = Histogram(
    "forno_order_ready_seconds",
    "Accepted -> READY duration.",
    labelnames=("channel",),
    buckets=_READY_BUCKETS,
)

promise_late_seconds = Histogram(
    "forno_promise_late_seconds",
    "Lateness beyond promised_at. Separate from early — histograms cannot hold negatives.",
    labelnames=("channel",),
    buckets=_READY_BUCKETS,
)

promise_early_seconds = Histogram(
    "forno_promise_early_seconds",
    "Earliness relative to promised_at.",
    labelnames=("channel",),
    buckets=_READY_BUCKETS,
)

orders_total = Counter(
    "forno_orders_total",
    "Orders by terminal outcome.",
    labelnames=("channel", "outcome"),  # outcome: served|cancelled|voided|abandoned|rejected
)

orders_open = Gauge(
    "forno_orders_open",
    "Orders placed but not yet in a terminal state — WIP, not a rate. Climbing "
    "with no matching climb in forno_orders_total means the kitchen/floor is "
    "falling behind, not that the pizzeria got busier.",
    labelnames=("channel",),
)

promise_breaches_total = Counter(
    "forno_promise_breaches_total",
    "Ready later than promised_at + grace.",
    labelnames=("channel",),
)

# --- Kitchen ---------------------------------------------------------------

kitchen_oldest_ticket_age_seconds = Gauge(
    "forno_kitchen_oldest_ticket_age_seconds",
    "Age, not depth — the SLI for kitchen backlog.",
    labelnames=("station",),
)

kitchen_tickets_open = Gauge(
    "forno_kitchen_tickets_open",
    "Context for age, never the primary signal.",
    labelnames=("station", "status"),
)

oven_slots_occupied = Gauge(
    "forno_oven_slots_occupied", "Occupied slots per oven.", labelnames=("oven",)
)

oven_slots_total = Gauge(
    "forno_oven_slots_total", "Configured slot count per oven.", labelnames=("oven",)
)

item_stage_seconds = Histogram(
    "forno_item_stage_seconds",
    "Time spent per stage: queue, prep, oven, pass.",
    labelnames=("stage",),
    buckets=_READY_BUCKETS,
)

fire_group_spread_seconds = Histogram(
    "forno_fire_group_spread_seconds",
    "First-to-last item READY within an order — how long the first pizza waits at the "
    "pass for its siblings. The metric the CP-SAT seam exists to improve.",
    buckets=(15, 30, 60, 90, 120, 180, 300, 600),
)

remakes_total = Counter("forno_remakes_total", "Remakes by reason.", labelnames=("reason",))

items_sold_total = Counter(
    "forno_items_sold_total", "Bounded by menu size.", labelnames=("menu_item", "size")
)

# --- Reliability -------------------------------------------------------------

outbox_pending = Gauge("forno_outbox_pending", "Unsent outbox rows.")

outbox_lag_seconds = Gauge(
    "forno_outbox_lag_seconds", "Age of the oldest unsent row. Leading indicator."
)

outbox_published_total = Counter(
    "forno_outbox_published_total", "Publish attempts by result.", labelnames=("result",)
)

relay_batch_seconds = Histogram("forno_relay_batch_seconds", "Poll-to-publish duration.")

events_consumed_total = Counter(
    "forno_events_consumed_total",
    "Consumed events by result: applied|duplicate|failed.",
    labelnames=("topic", "consumer", "result"),
)

consumer_lag_messages = Gauge(
    "forno_consumer_lag_messages",
    "High-water mark minus committed offset, observed by the consumer itself after each commit.",
    labelnames=("group", "topic", "partition"),
)

projection_lag_seconds = Gauge(
    "forno_projection_lag_seconds",
    "occurred_at -> applied. What a user would actually feel.",
    labelnames=("projector",),
)

dlq_messages_total = Counter(
    "forno_dlq_messages_total",
    "retryable|poison|caller_error — three different human responses.",
    labelnames=("topic", "classification"),
)

idempotency_replays_total = Counter(
    "forno_idempotency_replays_total",
    "Double-submits caught at the edge.",
    labelnames=("endpoint",),
)

# --- Edge --------------------------------------------------------------------

sse_connections = Gauge("forno_sse_connections", "Active SSE connections.", labelnames=("stream",))

sse_dropped_total = Counter(
    "forno_sse_dropped_total", "Dropped SSE subscribers by reason.", labelnames=("reason",)
)
