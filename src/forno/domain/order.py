"""The `Order` aggregate — the transactional boundary. See docs/domain-model.md
"Order lifecycle" and docs/adr/0006-kitchen-tickets-as-projection.md.

`KitchenTicket` is a read model projected from order events, not a second
write model. Chef commands are routed back through this aggregate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from forno.domain.exceptions import IllegalTransitionError, UnknownEntityError
from forno.domain.item import ItemStatus, OrderItem
from forno.domain.money import Currency, Money

if TYPE_CHECKING:
    from datetime import datetime

    from forno.domain.ids import OrderId, OrderItemId, TableId


class OrderStatus(StrEnum):
    PLACED = "PLACED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    IN_KITCHEN = "IN_KITCHEN"
    CANCELLED = "CANCELLED"
    READY = "READY"
    ABANDONED = "ABANDONED"
    SERVED = "SERVED"
    CLOSED = "CLOSED"
    VOIDED = "VOIDED"


class Channel(StrEnum):
    DINE_IN = "dine_in"
    TAKEAWAY = "takeaway"


# The transition table is the specification — see docs/domain-model.md "Order lifecycle".
_TRANSITIONS: Final[dict[OrderStatus, frozenset[OrderStatus]]] = {
    OrderStatus.PLACED: frozenset({OrderStatus.ACCEPTED, OrderStatus.REJECTED}),
    OrderStatus.ACCEPTED: frozenset({OrderStatus.IN_KITCHEN, OrderStatus.CANCELLED}),
    OrderStatus.IN_KITCHEN: frozenset(
        {OrderStatus.READY, OrderStatus.CANCELLED, OrderStatus.VOIDED}
    ),
    OrderStatus.READY: frozenset({OrderStatus.SERVED, OrderStatus.ABANDONED, OrderStatus.VOIDED}),
    OrderStatus.SERVED: frozenset({OrderStatus.CLOSED, OrderStatus.VOIDED}),
}

# Statuses in which chef commands (start / oven-in / ready / scrap) are legal.
_KITCHEN_ACTIVE: Final[frozenset[OrderStatus]] = frozenset(
    {OrderStatus.ACCEPTED, OrderStatus.IN_KITCHEN}
)


@dataclass(slots=True)
class Order:
    id: OrderId
    channel: Channel
    table_id: TableId | None
    status: OrderStatus
    items: list[OrderItem] = field(default_factory=list)
    placed_at: datetime | None = None
    promised_at: datetime | None = None  # set exactly once, at ACCEPTED — invariant #7
    ready_at: datetime | None = None
    version: int = 0  # optimistic lock

    @property
    def active_items(self) -> list[OrderItem]:
        """Every item still in play — excludes items scrapped and superseded
        by a remake. See docs/domain-model.md invariant #2."""
        return [item for item in self.items if item.status != ItemStatus.SCRAPPED]

    @property
    def has_entered_oven(self) -> bool:
        """True once any item — including one since scrapped, readied or
        delivered — has ever held an oven slot. Invariant #8 is about the
        committed physical cost of baked dough, not the current item status,
        so a scrap-after-oven or a READY sibling still counts."""
        return any(
            item.oven_id is not None
            or item.status in (ItemStatus.IN_OVEN, ItemStatus.READY, ItemStatus.DELIVERED)
            for item in self.items
        )

    @property
    def total_gross(self) -> Money:
        """Sum of `unit_price_snapshot * qty` with VAT, over non-voided lines.
        See docs/domain-model.md invariant #5."""
        billable = self.active_items
        if not billable:
            return Money(0, Currency.PLN)
        total = Money(0, billable[0].unit_price_snapshot.currency)
        for item in billable:
            total = total + item.unit_price_snapshot.with_vat(item.vat_rate_snapshot)
        return total

    def get_item(self, item_id: OrderItemId) -> OrderItem:
        for item in self.items:
            if item.id == item_id:
                return item
        msg = f"Item {item_id} not found on order {self.id}"
        raise UnknownEntityError(msg)

    def _transition(self, target: OrderStatus) -> None:
        """Raises IllegalTransitionError if `target` is not reachable from `status`.
        This is the single guarded chokepoint every public method below routes
        through — see docs/domain-model.md "Order lifecycle"."""
        if target not in _TRANSITIONS.get(self.status, frozenset()):
            msg = f"Order cannot transition {self.status} -> {target}"
            raise IllegalTransitionError(msg)
        self.status = target

    def accept(self, promised_at: datetime) -> None:
        self._transition(OrderStatus.ACCEPTED)
        self.promised_at = promised_at

    def reject(self, reason: str) -> None:
        del reason  # carried by the OrderRejected event, not stored on the aggregate
        self._transition(OrderStatus.REJECTED)

    def cancel(self) -> None:
        """Illegal once any item has entered IN_OVEN — dough is a committed
        physical cost. See docs/domain-model.md invariant #8."""
        if self.has_entered_oven:
            msg = f"Order {self.id} cannot be cancelled: an item has already entered the oven."
            raise IllegalTransitionError(msg)
        self._transition(OrderStatus.CANCELLED)

    def void(self, reason: str) -> None:
        """Manager only. The write-off for food that was made (or is being
        made) but will not be paid for: a walkout, a complaint after service.
        Legal once the kitchen has started — before that, `cancel` is the
        right verb and costs nothing. Distinct from `cancel`: the production
        record stays. See docs/domain-model.md "Order lifecycle"."""
        del reason  # carried by the OrderVoided event, not stored on the aggregate
        self._transition(OrderStatus.VOIDED)

    def close(self) -> None:
        self._transition(OrderStatus.CLOSED)

    def enter_kitchen(self) -> None:
        """ACCEPTED -> IN_KITCHEN, triggered when the first item starts prep.
        Idempotent: a no-op once the order is already IN_KITCHEN."""
        if self.status == OrderStatus.ACCEPTED:
            self._transition(OrderStatus.IN_KITCHEN)

    def evaluate_fire_together(self) -> None:
        """Order reaches READY iff every non-scrapped item is READY or
        DELIVERED. See docs/domain-model.md invariant #2 and "Fire-together"."""
        if self.status != OrderStatus.IN_KITCHEN:
            return
        active = self.active_items
        if active and all(
            item.status in (ItemStatus.READY, ItemStatus.DELIVERED) for item in active
        ):
            self._transition(OrderStatus.READY)

    def evaluate_service_complete(self) -> None:
        """READY -> SERVED once every active item has been delivered."""
        if self.status == OrderStatus.READY and all(
            item.status == ItemStatus.DELIVERED for item in self.active_items
        ):
            self._transition(OrderStatus.SERVED)

    def abandon(self) -> None:
        """READY -> ABANDONED. Walkout timeout."""
        self._transition(OrderStatus.ABANDONED)

    # --- Item commands, routed through the aggregate ---------------------------
    #
    # Chef and waiter commands address an item, but the *order* status decides
    # whether the kitchen is still working this order at all: a cancelled or
    # voided order must not quietly keep baking (or delivering) pizzas. The
    # item FSM alone cannot see that — hence these guards live here.

    def _require_in_progress(self) -> None:
        if self.status not in _KITCHEN_ACTIVE:
            msg = f"Order {self.id} is {self.status}; the kitchen is no longer working it"
            raise IllegalTransitionError(msg)

    def start_item(self, item_id: OrderItemId) -> OrderItem:
        self._require_in_progress()
        item = self.get_item(item_id)
        item.start_prep()
        self.enter_kitchen()
        return item

    def oven_in_item(self, item_id: OrderItemId, oven_id: str, slot: int) -> OrderItem:
        self._require_in_progress()
        item = self.get_item(item_id)
        item.allocate_oven(oven_id, slot)
        return item

    def mark_item_ready(self, item_id: OrderItemId) -> OrderItem:
        self._require_in_progress()
        item = self.get_item(item_id)
        item.mark_ready()
        self.evaluate_fire_together()
        return item

    def scrap_item(self, item_id: OrderItemId, reason: str) -> tuple[OrderItem, OrderItem]:
        """Returns `(scrapped, remake)`; the remake is appended to `items`."""
        self._require_in_progress()
        item = self.get_item(item_id)
        remake = item.scrap(reason)
        self.items.append(remake)
        return item, remake

    def deliver_item(self, item_id: OrderItemId) -> bool:
        """Returns True if the item changed state, False on an idempotent
        replay of an already-delivered item. Delivery requires the order to
        have reached READY — invariant #9: fire-together means nothing if a
        waiter can carry out the first pizza while the rest are still in
        the oven."""
        item = self.get_item(item_id)
        if item.status == ItemStatus.DELIVERED:
            return False
        if self.status not in (OrderStatus.READY, OrderStatus.SERVED):
            msg = f"Order {self.id} is {self.status}; items can only be delivered once it is READY"
            raise IllegalTransitionError(msg)
        item.deliver()
        self.evaluate_service_complete()
        return True
