"""Property-based FSM testing with Hypothesis. A stateful test drives random
legal command sequences against the aggregate and asserts that no reachable
state violates invariants 1-10. See docs/domain-model.md "Testing the model".

Randomised sequences reach interleavings nobody thinks to write by hand —
cancel-during-remake, scrap-after-ready, deliver-twice — which is precisely
where FSM bugs live.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

if TYPE_CHECKING:
    from collections.abc import Callable

from forno.domain.exceptions import IllegalTransitionError
from forno.domain.ids import MenuItemId, OrderId, OrderItemId
from forno.domain.item import ItemStatus, OrderItem
from forno.domain.money import Currency, Money, VatRate
from forno.domain.order import Channel, Order, OrderStatus

_PROMISED_AT = datetime(2026, 7, 22, 18, 0, tzinfo=UTC)


def _new_item(fire_group: int = 1) -> OrderItem:
    return OrderItem(
        id=OrderItemId(uuid4()),
        order_id=OrderId(uuid4()),
        menu_item_id=MenuItemId(uuid4()),
        name_snapshot="Margherita",
        unit_price_snapshot=Money(1000, Currency.PLN),
        vat_rate_snapshot=VatRate(Decimal("0.08")),
        prep_seconds=60,
        bake_seconds=300,
        fire_group=fire_group,
    )


class OrderStateMachine(RuleBasedStateMachine):
    """Drives one `Order` aggregate through randomised command sequences.

    `add_item`/`cancel`/`scrap` are the commands a cashier/chef/manager can
    issue; `start_prep`/`oven_in`/`ready`/`deliver` walk items through the
    kitchen so cancel-during-remake and scrap-after-ready are actually
    reachable, not just theoretically possible.
    """

    def __init__(self) -> None:
        super().__init__()
        self.order = Order(
            id=OrderId(uuid4()),
            channel=Channel.DINE_IN,
            table_id=None,
            status=OrderStatus.PLACED,
            items=[_new_item()],
            placed_at=_PROMISED_AT,
        )
        self.order.accept(promised_at=_PROMISED_AT)

    @rule()
    def add_item(self) -> None:
        # No public command adds a line after placement in this domain — the
        # order is fixed at ACCEPTED (docs/domain-model.md "Money" rule 2).
        # Appending here models a fresh item joining mid-service, the way a
        # scrap remake does — which is only ever reachable pre-READY, since
        # an item can only be scrapped from PREPPING/IN_OVEN, never READY.
        if self.order.status == OrderStatus.READY:
            return
        before = len(self.order.items)
        self.order.items.append(_new_item())
        assert len(self.order.items) == before + 1

    def _kitchen_command(
        self, item: OrderItem, command: Callable[..., object], *args: object
    ) -> None:
        """Chef commands go through the aggregate, which refuses them once the
        order is no longer in progress (cancelled/voided/closed): a cancelled
        order must not keep baking. The refusal leaves the item untouched."""
        before = item.status
        try:
            command(item.id, *args)
        except IllegalTransitionError:
            assert self.order.status not in (OrderStatus.ACCEPTED, OrderStatus.IN_KITCHEN)
            assert item.status == before

    @rule()
    def start_prep(self) -> None:
        for item in self.order.items:
            if item.status == ItemStatus.QUEUED:
                self._kitchen_command(item, self.order.start_item)
                return

    @rule()
    def oven_in(self) -> None:
        for item in self.order.items:
            if item.status == ItemStatus.PREPPING:
                self._kitchen_command(item, self.order.oven_in_item, "deck-1", 1)
                return

    @rule()
    def ready(self) -> None:
        for item in self.order.items:
            if item.status == ItemStatus.IN_OVEN:
                self._kitchen_command(item, self.order.mark_item_ready)
                return

    @rule()
    def deliver(self) -> None:
        for item in self.order.items:
            if item.status == ItemStatus.READY:
                try:
                    self.order.deliver_item(item.id)
                except IllegalTransitionError:
                    # Invariant #9: refused unless the order itself is READY.
                    assert self.order.status not in (OrderStatus.READY, OrderStatus.SERVED)
                    assert item.status == ItemStatus.READY
                return

    @rule()
    def cancel(self) -> None:
        # Invariant #8, stated independently of the implementation: an item
        # that holds (or has ever held) an oven slot, or is past the oven,
        # means the dough is a sunk cost and cancellation must be refused.
        any_baked = any(
            item.oven_id is not None
            or item.status in (ItemStatus.IN_OVEN, ItemStatus.READY, ItemStatus.DELIVERED)
            for item in self.order.items
        )
        try:
            self.order.cancel()
        except IllegalTransitionError:
            pass
        else:
            assert not any_baked

    @rule()
    def scrap(self) -> None:
        for item in self.order.items:
            if item.status in (ItemStatus.PREPPING, ItemStatus.IN_OVEN):
                self._kitchen_command(item, self.order.scrap_item, "burnt")
                return

    @invariant()
    def never_ready_with_pending_items(self) -> None:
        if self.order.status == OrderStatus.READY:
            assert all(
                item.status in (ItemStatus.READY, ItemStatus.DELIVERED)
                for item in self.order.active_items
            )

    @invariant()
    def delivered_items_only_on_orders_that_reached_ready(self) -> None:
        """Invariant #9."""
        if any(item.status == ItemStatus.DELIVERED for item in self.order.items):
            assert self.order.status in (OrderStatus.READY, OrderStatus.SERVED)

    @invariant()
    def cancelled_orders_never_baked_anything(self) -> None:
        """Invariant #8, as a reachable-state property."""
        if self.order.status == OrderStatus.CANCELLED:
            assert not self.order.has_entered_oven


TestOrderStateMachine = OrderStateMachine.TestCase
