"""Explicit mapping functions between `forno.domain` objects and the wire
DTOs in this package. Costs twenty lines, buys the freedom to reshape the
domain whenever the domain needs reshaping. See docs/architecture.md
"Wire contracts are not domain objects".
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from forno.contracts.events import (
    ItemStateChangedV1,
    OrderAcceptedV1,
    OrderCancelledV1,
    OrderClosedV1,
    OrderLineV1,
    OrderPlacedV1,
    OrderReadyV1,
    OrderRejectedV1,
    OrderServedV1,
    OrderVoidedV1,
)
from forno.contracts.responses import OrderItemResponse, OrderResponse

if TYPE_CHECKING:
    from forno.domain.events import (
        ItemStateChanged,
        OrderAccepted,
        OrderCancelled,
        OrderClosed,
        OrderPlaced,
        OrderReady,
        OrderRejected,
        OrderServed,
        OrderVoided,
    )
    from forno.domain.order import Order


def order_placed_to_v1(event: OrderPlaced, correlation_id: str) -> OrderPlacedV1:
    return OrderPlacedV1(
        event_id=uuid4(),
        occurred_at=event.occurred_at,
        order_id=event.order_id,
        channel=event.channel,
        items=[
            OrderLineV1(
                order_item_id=line.item_id,
                menu_item_id=line.menu_item_id,
                name_snapshot=line.name_snapshot,
                unit_price_snapshot=line.unit_price_snapshot.to_decimal(),
                fire_group=line.fire_group,
            )
            for line in event.items
        ],
        correlation_id=correlation_id,
    )


def order_accepted_to_v1(event: OrderAccepted, correlation_id: str) -> OrderAcceptedV1:
    return OrderAcceptedV1(
        event_id=uuid4(),
        occurred_at=event.occurred_at,
        order_id=event.order_id,
        promised_at=event.promised_at,
        correlation_id=correlation_id,
    )


def order_rejected_to_v1(event: OrderRejected, correlation_id: str) -> OrderRejectedV1:
    return OrderRejectedV1(
        event_id=uuid4(),
        occurred_at=event.occurred_at,
        order_id=event.order_id,
        reason=event.reason,
        correlation_id=correlation_id,
    )


def order_cancelled_to_v1(event: OrderCancelled, correlation_id: str) -> OrderCancelledV1:
    return OrderCancelledV1(
        event_id=uuid4(),
        occurred_at=event.occurred_at,
        order_id=event.order_id,
        correlation_id=correlation_id,
    )


def order_ready_to_v1(event: OrderReady, correlation_id: str) -> OrderReadyV1:
    return OrderReadyV1(
        event_id=uuid4(),
        occurred_at=event.occurred_at,
        order_id=event.order_id,
        correlation_id=correlation_id,
    )


def order_served_to_v1(event: OrderServed, correlation_id: str) -> OrderServedV1:
    return OrderServedV1(
        event_id=uuid4(),
        occurred_at=event.occurred_at,
        order_id=event.order_id,
        correlation_id=correlation_id,
    )


def order_closed_to_v1(event: OrderClosed, correlation_id: str) -> OrderClosedV1:
    return OrderClosedV1(
        event_id=uuid4(),
        occurred_at=event.occurred_at,
        order_id=event.order_id,
        correlation_id=correlation_id,
    )


def order_voided_to_v1(event: OrderVoided, correlation_id: str) -> OrderVoidedV1:
    return OrderVoidedV1(
        event_id=uuid4(),
        occurred_at=event.occurred_at,
        order_id=event.order_id,
        reason=event.reason,
        correlation_id=correlation_id,
    )


def item_state_changed_to_v1(event: ItemStateChanged, correlation_id: str) -> ItemStateChangedV1:
    return ItemStateChangedV1(
        event_id=uuid4(),
        occurred_at=event.occurred_at,
        order_id=event.order_id,
        item_id=event.item_id,
        ticket_id=event.ticket_id,
        status=event.status,
        correlation_id=correlation_id,
    )


def order_to_response(order: Order, currency: str) -> OrderResponse:
    return OrderResponse(
        id=order.id,
        status=order.status,
        channel=order.channel,
        table_number=order.table_id,
        placed_at=order.placed_at,
        promised_at=order.promised_at,
        ready_at=order.ready_at,
        total_gross=order.total_gross.to_decimal(),
        currency=currency,
        items=[
            OrderItemResponse(
                id=item.id,
                menu_item_id=item.menu_item_id,
                name=item.name_snapshot,
                unit_price=item.unit_price_snapshot.to_decimal(),
                status=item.status,
                fire_group=item.fire_group,
                remake_of=item.remake_of,
            )
            for item in order.items
        ],
    )
