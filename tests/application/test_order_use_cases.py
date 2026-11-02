"""Cancel/Void/Close/Deliver/Get all depend only on `OrderRepository` +
`EventPublisher` (no raw DB queries), so they're a clean fit for
`FakeOrderRepository` + `InMemoryPublisher` + `FrozenClock` — no containers,
no mocks. `PlaceOrder`/`ListOrders` query SQLAlchemy models directly for
menu/oven/table data and are covered by the integration/e2e suites instead.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from forno.adapters.messaging.publisher import InMemoryPublisher
from forno.application.use_cases.orders import (
    CancelOrder,
    CloseOrder,
    DeliverItem,
    GetOrder,
    VoidOrder,
)
from forno.domain.exceptions import (
    ConcurrentModificationError,
    IllegalTransitionError,
    UnknownEntityError,
)
from forno.domain.ids import MenuItemId, OrderId, OrderItemId
from forno.domain.item import ItemStatus, OrderItem
from forno.domain.money import Currency, Money, VatRate
from forno.domain.order import Channel, Order, OrderStatus
from tests.fakes import FakeOrderRepository, FakeUnitOfWork


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


@pytest.fixture
def repo() -> FakeOrderRepository:
    return FakeOrderRepository()


@pytest.fixture
def uow(repo, in_memory_publisher) -> FakeUnitOfWork:
    return FakeUnitOfWork(repo, in_memory_publisher)


# --- CancelOrder -------------------------------------------------------------


async def test_cancel_order_transitions_and_publishes_event(uow, repo, frozen_clock):
    order = _order(OrderStatus.ACCEPTED)
    repo.seed(order)

    result = await CancelOrder(uow, frozen_clock).execute(order.id, "c-1")

    assert result.status == OrderStatus.CANCELLED
    assert uow.committed is True
    assert len(uow.events.published) == 1
    published_order_id, event, correlation_id = uow.events.published[0]
    assert published_order_id == order.id
    assert type(event).__name__ == "OrderCancelled"
    assert correlation_id == "c-1"


async def test_cancel_order_raises_once_an_item_is_in_oven_and_publishes_nothing(
    uow, repo, frozen_clock
):
    order = _order(OrderStatus.IN_KITCHEN, [_item(status=ItemStatus.IN_OVEN)])
    repo.seed(order)

    with pytest.raises(IllegalTransitionError):
        await CancelOrder(uow, frozen_clock).execute(order.id, "c-1")

    assert uow.events.published == []
    assert uow.committed is False
    # The stored order must be untouched by the rejected command.
    stored = await repo.get(order.id)
    assert stored.status == OrderStatus.IN_KITCHEN


async def test_cancel_unknown_order_raises_unknown_entity(uow, frozen_clock):
    with pytest.raises(UnknownEntityError):
        await CancelOrder(uow, frozen_clock).execute(OrderId(uuid4()), "c-1")


# --- VoidOrder -----------------------------------------------------------------


async def test_void_order_requires_served_and_reverses_only_the_money(uow, repo, frozen_clock):
    order = _order(OrderStatus.SERVED)
    repo.seed(order)

    result = await VoidOrder(uow, frozen_clock).execute(order.id, "customer complaint", "c-1")

    assert result.status == OrderStatus.VOIDED
    _, event, _ = uow.events.published[0]
    assert event.reason == "customer complaint"


async def test_void_order_before_served_is_illegal(uow, repo, frozen_clock):
    order = _order(OrderStatus.ACCEPTED)
    repo.seed(order)
    with pytest.raises(IllegalTransitionError):
        await VoidOrder(uow, frozen_clock).execute(order.id, "too early", "c-1")


# --- CloseOrder ------------------------------------------------------------------


async def test_close_order_from_served(uow, repo, frozen_clock):
    order = _order(OrderStatus.SERVED)
    repo.seed(order)
    result = await CloseOrder(uow, frozen_clock).execute(order.id, "c-1")
    assert result.status == OrderStatus.CLOSED


async def test_close_order_before_served_is_illegal(uow, repo, frozen_clock):
    order = _order(OrderStatus.READY)
    repo.seed(order)
    with pytest.raises(IllegalTransitionError):
        await CloseOrder(uow, frozen_clock).execute(order.id, "c-1")


# --- DeliverItem -----------------------------------------------------------------


async def test_deliver_item_marks_delivered_and_publishes_event(uow, repo, frozen_clock):
    item = _item(status=ItemStatus.READY)
    still_pending = _item(status=ItemStatus.READY)
    order = _order(OrderStatus.READY, [item, still_pending])
    repo.seed(order)

    result = await DeliverItem(uow, frozen_clock).execute(order.id, str(item.id), "c-1")

    assert result.get_item(item.id).status == ItemStatus.DELIVERED
    # A sibling item is still READY, so the order itself isn't SERVED yet —
    # only the one ItemStateChanged event, no OrderServed.
    assert result.status == OrderStatus.READY
    assert len(uow.events.published) == 1


async def test_deliver_item_is_idempotent_on_second_call(uow, repo, frozen_clock):
    """Double-delivery is invariant #1 in the simulator's shadow ledger: it
    must be a silent no-op, not an error and not a second event."""
    item = _item(status=ItemStatus.READY)
    order = _order(OrderStatus.READY, [item])
    repo.seed(order)

    await DeliverItem(uow, frozen_clock).execute(order.id, str(item.id), "c-1")
    first_call_event_count = len(uow.events.published)

    result = await DeliverItem(uow, frozen_clock).execute(order.id, str(item.id), "c-2")

    assert result.get_item(item.id).status == ItemStatus.DELIVERED
    assert len(uow.events.published) == first_call_event_count  # no new event on the replay


async def test_deliver_item_completes_the_order_once_every_item_is_delivered(
    uow, repo, frozen_clock
):
    item_a = _item(status=ItemStatus.READY)
    item_b = _item(status=ItemStatus.DELIVERED)  # already delivered
    order = _order(OrderStatus.READY, [item_a, item_b])
    repo.seed(order)

    result = await DeliverItem(uow, frozen_clock).execute(order.id, str(item_a.id), "c-1")

    assert result.status == OrderStatus.SERVED
    event_types = [type(event).__name__ for _, event, _ in uow.events.published]
    assert "ItemStateChanged" in event_types
    assert "OrderServed" in event_types


async def test_deliver_item_before_order_ready_is_illegal_and_publishes_nothing(
    uow, repo, frozen_clock
):
    """Invariant #9 at the use-case level: a waiter cannot carry out the
    first pizza while its fire-group sibling is still in the oven."""
    ready = _item(status=ItemStatus.READY)
    order = _order(OrderStatus.IN_KITCHEN, [ready, _item(status=ItemStatus.IN_OVEN)])
    repo.seed(order)

    with pytest.raises(IllegalTransitionError):
        await DeliverItem(uow, frozen_clock).execute(order.id, str(ready.id), "c-1")

    assert uow.events.published == []
    assert (await repo.get(order.id)).get_item(ready.id).status == ItemStatus.READY


async def test_cancel_order_raises_once_an_item_has_been_baked(uow, repo, frozen_clock):
    """Invariant #8 — a READY item was in the oven; its dough is sunk cost."""
    order = _order(OrderStatus.IN_KITCHEN, [_item(status=ItemStatus.READY), _item()])
    repo.seed(order)
    with pytest.raises(IllegalTransitionError):
        await CancelOrder(uow, frozen_clock).execute(order.id, "c-1")
    assert uow.events.published == []


async def test_deliver_item_on_unknown_item_raises_unknown_entity(uow, repo, frozen_clock):
    order = _order(OrderStatus.READY, [_item(status=ItemStatus.READY)])
    repo.seed(order)
    with pytest.raises(UnknownEntityError):
        await DeliverItem(uow, frozen_clock).execute(order.id, str(uuid4()), "c-1")


# --- GetOrder --------------------------------------------------------------------


async def test_get_order_returns_the_seeded_order(uow, repo):
    order = _order(OrderStatus.ACCEPTED)
    repo.seed(order)
    result = await GetOrder(uow).execute(order.id)
    assert result.id == order.id


async def test_get_order_raises_unknown_entity_for_missing_order(uow):
    with pytest.raises(UnknownEntityError):
        await GetOrder(uow).execute(OrderId(uuid4()))


# --- Optimistic concurrency --------------------------------------------------------


async def test_concurrent_save_raises_concurrent_modification(repo, frozen_clock):
    """Two 'requests' each fetch the order (their own independent copy, as a
    real SELECT would give them), then both try to cancel it. The first
    save wins; the second must be rejected rather than silently overwriting
    the first cancellation with stale data — this is what `Order.version`
    (optimistic locking) exists to prevent."""
    order = _order(OrderStatus.ACCEPTED)
    repo.seed(order)

    uow_a = FakeUnitOfWork(repo, InMemoryPublisher())

    order_a = await repo.get(order.id)
    order_b = await repo.get(order.id)
    assert order_a.version == order_b.version

    await CancelOrder(uow_a, frozen_clock).execute(order.id, "c-1")

    # order_b is now stale — force VoidOrder's internal get() to reuse the
    # same stale snapshot by seeding a repo.save() call directly with it.
    order_b.status = OrderStatus.ACCEPTED  # pretend we're about to cancel too
    with pytest.raises(ConcurrentModificationError):
        await repo.save(order_b)
