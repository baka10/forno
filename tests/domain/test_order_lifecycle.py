"""Test names are the specification — see CONTRIBUTING.md "Testing strategy"
and docs/domain-model.md "Testing the model".
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from forno.domain.exceptions import IllegalTransitionError
from forno.domain.ids import MenuItemId, OrderId, OrderItemId
from forno.domain.item import ItemStatus, OrderItem
from forno.domain.money import Currency, Money, VatRate
from forno.domain.order import Channel, Order, OrderStatus

_PROMISED_AT = datetime(2026, 7, 22, 18, 0, tzinfo=UTC)


def _item(status: ItemStatus = ItemStatus.QUEUED, fire_group: int = 1) -> OrderItem:
    return OrderItem(
        id=OrderItemId(uuid4()),
        order_id=OrderId(uuid4()),
        menu_item_id=MenuItemId(uuid4()),
        name_snapshot="Margherita",
        unit_price_snapshot=Money(3200, Currency.PLN),
        vat_rate_snapshot=VatRate(Decimal("0.08")),
        prep_seconds=60,
        bake_seconds=300,
        fire_group=fire_group,
        status=status,
    )


def _order(status: OrderStatus, items: list[OrderItem] | None = None) -> Order:
    return Order(
        id=OrderId(uuid4()),
        channel=Channel.DINE_IN,
        table_id=None,
        status=status,
        items=items if items is not None else [],
    )


def test_cannot_cancel_once_in_oven():
    """Invariant #8 — docs/domain-model.md."""
    order = _order(OrderStatus.IN_KITCHEN, [_item(status=ItemStatus.IN_OVEN)])
    with pytest.raises(IllegalTransitionError):
        order.cancel()
    assert order.status == OrderStatus.IN_KITCHEN


def test_cannot_cancel_once_an_item_has_been_baked_even_if_now_ready():
    """Invariant #8 is about dough that went into the oven, not the item's
    current status: a READY sibling or a scrapped-after-oven item still
    makes cancellation illegal."""
    baked = _item(status=ItemStatus.READY)
    order = _order(OrderStatus.IN_KITCHEN, [baked, _item(status=ItemStatus.PREPPING)])
    with pytest.raises(IllegalTransitionError):
        order.cancel()

    scrapped_after_oven = _item(status=ItemStatus.IN_OVEN)
    scrapped_after_oven.oven_id, scrapped_after_oven.oven_slot = "deck-1", 1
    scrapped_after_oven.scrap("burnt")
    order = _order(OrderStatus.IN_KITCHEN, [scrapped_after_oven])
    with pytest.raises(IllegalTransitionError):
        order.cancel()


def test_cannot_deliver_an_item_before_the_order_is_ready():
    """Invariant #9 — fire-together: the first pizza waits for the rest."""
    ready, pending = _item(status=ItemStatus.READY), _item(status=ItemStatus.IN_OVEN)
    order = _order(OrderStatus.IN_KITCHEN, [ready, pending])
    with pytest.raises(IllegalTransitionError):
        order.deliver_item(ready.id)
    assert ready.status == ItemStatus.READY


def test_deliver_item_is_idempotent_and_completes_service():
    first, second = _item(status=ItemStatus.READY), _item(status=ItemStatus.READY)
    order = _order(OrderStatus.READY, [first, second])
    assert order.deliver_item(first.id) is True
    assert order.deliver_item(first.id) is False  # replay: no-op, no error
    assert order.status == OrderStatus.READY
    assert order.deliver_item(second.id) is True
    assert order.status == OrderStatus.SERVED


@pytest.mark.parametrize("status", [OrderStatus.CANCELLED, OrderStatus.VOIDED, OrderStatus.CLOSED])
def test_kitchen_commands_are_refused_once_the_order_is_no_longer_in_progress(status):
    """A cancelled order must not keep baking: chef commands are routed
    through the aggregate so the order status can refuse them."""
    queued, prepping = _item(status=ItemStatus.QUEUED), _item(status=ItemStatus.PREPPING)
    order = _order(status, [queued, prepping])
    with pytest.raises(IllegalTransitionError):
        order.start_item(queued.id)
    with pytest.raises(IllegalTransitionError):
        order.oven_in_item(prepping.id, "deck-1", 1)
    with pytest.raises(IllegalTransitionError):
        order.scrap_item(prepping.id, "burnt")
    assert queued.status == ItemStatus.QUEUED
    assert prepping.status == ItemStatus.PREPPING


def test_start_item_moves_order_into_kitchen():
    item = _item()
    order = _order(OrderStatus.ACCEPTED, [item])
    order.start_item(item.id)
    assert item.status == ItemStatus.PREPPING
    assert order.status == OrderStatus.IN_KITCHEN


def test_scrap_item_appends_the_remake_to_the_order():
    item = _item(status=ItemStatus.IN_OVEN)
    order = _order(OrderStatus.IN_KITCHEN, [item])
    scrapped, remake = order.scrap_item(item.id, "burnt")
    assert scrapped is item
    assert remake in order.items
    assert remake.remake_of == item.id


def test_cancel_before_any_item_in_oven_succeeds():
    order = _order(OrderStatus.IN_KITCHEN, [_item(status=ItemStatus.PREPPING)])
    order.cancel()
    assert order.status == OrderStatus.CANCELLED


def test_cancel_with_no_items_in_accepted_succeeds():
    order = _order(OrderStatus.ACCEPTED)
    order.cancel()
    assert order.status == OrderStatus.CANCELLED


def test_illegal_transition_raises_not_returns_false():
    """Illegal transitions are exceptions (-> 409 at the edge), never a
    silent no-op. See docs/domain-model.md "Order lifecycle"."""
    order = _order(OrderStatus.PLACED)
    with pytest.raises(IllegalTransitionError):
        order.close()
    # The order is untouched — a rejected command must not leave partial state.
    assert order.status == OrderStatus.PLACED


@pytest.mark.parametrize(
    ("start", "action"),
    [
        (OrderStatus.PLACED, lambda o: o.close()),
        (OrderStatus.READY, lambda o: o.cancel()),
        (OrderStatus.SERVED, lambda o: o.cancel()),
        (OrderStatus.CLOSED, lambda o: o.accept(_PROMISED_AT)),
        (OrderStatus.REJECTED, lambda o: o.accept(_PROMISED_AT)),
    ],
)
def test_every_illegal_transition_raises_and_leaves_status_untouched(start, action):
    order = _order(start)
    with pytest.raises(IllegalTransitionError):
        action(order)
    assert order.status == start


def test_promised_at_set_exactly_once_at_accepted():
    """Invariant #7."""
    order = _order(OrderStatus.PLACED)
    assert order.promised_at is None
    order.accept(_PROMISED_AT)
    assert order.promised_at == _PROMISED_AT
    assert order.status == OrderStatus.ACCEPTED

    # ACCEPTED cannot legally re-enter ACCEPTED, so promised_at cannot be
    # silently recomputed by calling accept() again.
    with pytest.raises(IllegalTransitionError):
        order.accept(datetime(2099, 1, 1, tzinfo=UTC))
    assert order.promised_at == _PROMISED_AT


def test_order_ready_iff_every_non_scrapped_item_ready_or_delivered():
    """Invariant #2."""
    one_pending = _order(
        OrderStatus.IN_KITCHEN,
        [_item(status=ItemStatus.READY), _item(status=ItemStatus.IN_OVEN)],
    )
    one_pending.evaluate_fire_together()
    assert one_pending.status == OrderStatus.IN_KITCHEN

    all_done = _order(
        OrderStatus.IN_KITCHEN,
        [_item(status=ItemStatus.READY), _item(status=ItemStatus.DELIVERED)],
    )
    all_done.evaluate_fire_together()
    assert all_done.status == OrderStatus.READY


def test_order_ready_ignores_scrapped_items():
    order = _order(
        OrderStatus.IN_KITCHEN,
        [_item(status=ItemStatus.READY), _item(status=ItemStatus.SCRAPPED)],
    )
    order.evaluate_fire_together()
    assert order.status == OrderStatus.READY


def test_order_with_no_active_items_does_not_spuriously_become_ready():
    # All items scrapped, none replaced yet — nothing to be "ready" about.
    order = _order(OrderStatus.IN_KITCHEN, [_item(status=ItemStatus.SCRAPPED)])
    order.evaluate_fire_together()
    assert order.status == OrderStatus.IN_KITCHEN


def test_voided_is_not_cancelled():
    """docs/domain-model.md "Order lifecycle" — voiding requires manager role
    and writes off food that was made; it keeps the production record."""
    order = _order(OrderStatus.SERVED)
    order.void("customer complaint")
    assert order.status == OrderStatus.VOIDED
    assert order.status != OrderStatus.CANCELLED

    # Before the kitchen has started nothing was made: cancel, never void.
    not_started = _order(OrderStatus.ACCEPTED)
    with pytest.raises(IllegalTransitionError):
        not_started.void("too early")


@pytest.mark.parametrize("status", [OrderStatus.IN_KITCHEN, OrderStatus.READY])
def test_walkout_is_voided_once_food_is_being_made(status):
    """A guest who leaves after the dough went in: cancel is refused
    (invariant #8), so the manager voids — the order ends in a terminal
    state instead of occupying the kitchen forever."""
    order = _order(status, [_item(status=ItemStatus.IN_OVEN)])
    with pytest.raises(IllegalTransitionError):
        order.cancel()
    order.void("walkout")
    assert order.status == OrderStatus.VOIDED


def test_enter_kitchen_is_idempotent():
    order = _order(OrderStatus.ACCEPTED, [_item()])
    order.enter_kitchen()
    assert order.status == OrderStatus.IN_KITCHEN
    order.enter_kitchen()  # second item starting prep — must not raise
    assert order.status == OrderStatus.IN_KITCHEN


def test_evaluate_service_complete_requires_every_item_delivered():
    order = _order(
        OrderStatus.READY,
        [_item(status=ItemStatus.DELIVERED), _item(status=ItemStatus.READY)],
    )
    order.evaluate_service_complete()
    assert order.status == OrderStatus.READY

    order.items[1].status = ItemStatus.DELIVERED
    order.evaluate_service_complete()
    assert order.status == OrderStatus.SERVED


def test_get_item_raises_unknown_entity_for_missing_item():
    from forno.domain.exceptions import UnknownEntityError

    order = _order(OrderStatus.ACCEPTED, [_item()])
    with pytest.raises(UnknownEntityError):
        order.get_item(OrderItemId(uuid4()))


def test_get_item_returns_the_matching_item():
    target = _item()
    order = _order(OrderStatus.ACCEPTED, [_item(), target, _item()])
    assert order.get_item(target.id) is target


def test_reject_from_placed():
    order = _order(OrderStatus.PLACED)
    order.reject("menu item unavailable")
    assert order.status == OrderStatus.REJECTED


def test_close_from_served_succeeds():
    order = _order(OrderStatus.SERVED)
    order.close()
    assert order.status == OrderStatus.CLOSED


def test_abandon_from_ready():
    order = _order(OrderStatus.READY)
    order.abandon()
    assert order.status == OrderStatus.ABANDONED
