from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from forno.contracts import mapping
from forno.domain.events import (
    ItemStateChanged,
    OrderAccepted,
    OrderCancelled,
    OrderClosed,
    OrderPlaced,
    OrderPlacedLine,
    OrderReady,
    OrderRejected,
    OrderServed,
    OrderVoided,
)
from forno.domain.ids import KitchenTicketId, MenuItemId, OrderId, OrderItemId
from forno.domain.item import ItemStatus, OrderItem
from forno.domain.money import Currency, Money, VatRate
from forno.domain.order import Channel, Order, OrderStatus

_NOW = datetime(2026, 7, 22, 18, 0, tzinfo=UTC)
_CORRELATION_ID = "c-91"


def test_order_placed_to_v1_maps_lines_and_converts_money_to_decimal():
    order_id = OrderId(uuid4())
    item_id = OrderItemId(uuid4())
    menu_item_id = MenuItemId(uuid4())
    event = OrderPlaced(
        order_id=order_id,
        channel="dine_in",
        items=(
            OrderPlacedLine(
                item_id=item_id,
                menu_item_id=menu_item_id,
                name_snapshot="Margherita",
                unit_price_snapshot=Money(3200, Currency.PLN),
                fire_group=1,
            ),
        ),
        occurred_at=_NOW,
    )

    wire = mapping.order_placed_to_v1(event, _CORRELATION_ID)

    assert wire.schema_version == 1
    assert isinstance(wire.event_id, UUID)
    assert wire.order_id == order_id
    assert wire.channel == "dine_in"
    assert wire.correlation_id == _CORRELATION_ID
    assert len(wire.items) == 1
    line = wire.items[0]
    assert line.order_item_id == item_id
    assert line.menu_item_id == menu_item_id
    assert line.unit_price_snapshot == Decimal("32.00")
    assert line.fire_group == 1


def test_order_placed_to_v1_generates_a_fresh_event_id_each_call():
    event = OrderPlaced(order_id=OrderId(uuid4()), channel="dine_in", items=(), occurred_at=_NOW)
    first = mapping.order_placed_to_v1(event, _CORRELATION_ID)
    second = mapping.order_placed_to_v1(event, _CORRELATION_ID)
    assert first.event_id != second.event_id


def test_order_accepted_to_v1_carries_promised_at():
    order_id = OrderId(uuid4())
    promised_at = _NOW.replace(hour=19)
    event = OrderAccepted(order_id=order_id, promised_at=promised_at, occurred_at=_NOW)
    wire = mapping.order_accepted_to_v1(event, _CORRELATION_ID)
    assert wire.order_id == order_id
    assert wire.promised_at == promised_at


def test_item_state_changed_to_v1_carries_ticket_id_and_status():
    order_id = OrderId(uuid4())
    item_id = OrderItemId(uuid4())
    ticket_id = KitchenTicketId(uuid4())
    event = ItemStateChanged(
        order_id=order_id,
        item_id=item_id,
        ticket_id=ticket_id,
        status="IN_OVEN",
        occurred_at=_NOW,
    )
    wire = mapping.item_state_changed_to_v1(event, _CORRELATION_ID)
    assert wire.order_id == order_id
    assert wire.item_id == item_id
    assert wire.ticket_id == ticket_id
    assert wire.status == "IN_OVEN"


def test_item_state_changed_to_v1_allows_no_ticket_id():
    """Not every item event necessarily has a ticket yet (e.g. before the
    kitchen projector has caught up)."""
    event = ItemStateChanged(
        order_id=OrderId(uuid4()),
        item_id=OrderItemId(uuid4()),
        ticket_id=None,
        status="DELIVERED",
        occurred_at=_NOW,
    )
    wire = mapping.item_state_changed_to_v1(event, _CORRELATION_ID)
    assert wire.ticket_id is None


@pytest.mark.parametrize(
    ("event", "mapper", "carries_reason"),
    [
        (
            OrderRejected(
                order_id=OrderId(uuid4()), reason="menu item unavailable", occurred_at=_NOW
            ),
            mapping.order_rejected_to_v1,
            True,
        ),
        (
            OrderCancelled(order_id=OrderId(uuid4()), occurred_at=_NOW),
            mapping.order_cancelled_to_v1,
            False,
        ),
        (OrderReady(order_id=OrderId(uuid4()), occurred_at=_NOW), mapping.order_ready_to_v1, False),
        (
            OrderServed(order_id=OrderId(uuid4()), occurred_at=_NOW),
            mapping.order_served_to_v1,
            False,
        ),
        (
            OrderClosed(order_id=OrderId(uuid4()), occurred_at=_NOW),
            mapping.order_closed_to_v1,
            False,
        ),
        (
            OrderVoided(order_id=OrderId(uuid4()), reason="comped", occurred_at=_NOW),
            mapping.order_voided_to_v1,
            True,
        ),
    ],
)
def test_lifecycle_event_mappers_carry_order_id_and_correlation_id(event, mapper, carries_reason):
    wire = mapper(event, _CORRELATION_ID)
    assert wire.order_id == event.order_id
    assert wire.correlation_id == _CORRELATION_ID
    assert wire.occurred_at == _NOW
    if carries_reason:
        assert wire.reason == event.reason


def test_order_to_response_uses_the_domain_computed_total_not_a_raw_sum():
    item = OrderItem(
        id=OrderItemId(uuid4()),
        order_id=OrderId(uuid4()),
        menu_item_id=MenuItemId(uuid4()),
        name_snapshot="Margherita",
        unit_price_snapshot=Money(3200, Currency.PLN),
        vat_rate_snapshot=VatRate(Decimal("0.08")),
        prep_seconds=60,
        bake_seconds=300,
        fire_group=1,
        status=ItemStatus.QUEUED,
    )
    order = Order(
        id=OrderId(uuid4()),
        channel=Channel.TAKEAWAY,
        table_id=None,
        status=OrderStatus.PLACED,
        items=[item],
        placed_at=_NOW,
    )

    response = mapping.order_to_response(order, "PLN")

    assert response.total_gross == Decimal("34.56")  # 32.00 * 1.08, VAT included
    assert response.channel == Channel.TAKEAWAY
    assert response.status == OrderStatus.PLACED
    assert response.currency == "PLN"
    assert len(response.items) == 1
    assert response.items[0].unit_price == Decimal("32.00")
    assert response.items[0].status == ItemStatus.QUEUED
    assert response.items[0].remake_of is None


def test_order_to_response_carries_remake_of_through():
    original_id = OrderItemId(uuid4())
    remake = OrderItem(
        id=OrderItemId(uuid4()),
        order_id=OrderId(uuid4()),
        menu_item_id=MenuItemId(uuid4()),
        name_snapshot="Margherita",
        unit_price_snapshot=Money(3200, Currency.PLN),
        vat_rate_snapshot=VatRate(Decimal("0.08")),
        prep_seconds=60,
        bake_seconds=300,
        fire_group=1,
        status=ItemStatus.QUEUED,
        remake_of=original_id,
    )
    order = Order(
        id=OrderId(uuid4()),
        channel=Channel.DINE_IN,
        table_id=None,
        status=OrderStatus.IN_KITCHEN,
        items=[remake],
        placed_at=_NOW,
    )
    response = mapping.order_to_response(order, "PLN")
    assert response.items[0].remake_of == original_id
