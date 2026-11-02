"""Order item lifecycle. See docs/domain-model.md "Item lifecycle".

An item is the grain the kitchen actually works on — different stations,
different speeds — synchronised per order via `fire_group`
(docs/domain-model.md "Fire-together").
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from forno.domain.exceptions import CapacityExceededError, IllegalTransitionError
from forno.domain.ids import MenuItemId, OrderId, OrderItemId

if TYPE_CHECKING:
    from forno.domain.money import Money, VatRate


class ItemStatus(StrEnum):
    QUEUED = "QUEUED"
    PREPPING = "PREPPING"
    IN_OVEN = "IN_OVEN"
    READY = "READY"
    DELIVERED = "DELIVERED"
    SCRAPPED = "SCRAPPED"


class Priority(StrEnum):
    NORMAL = "NORMAL"
    HIGH = "HIGH"  # remakes — see docs/domain-model.md "Item lifecycle"


_FINISHED_STATUSES: Final[frozenset[ItemStatus]] = frozenset(
    {ItemStatus.READY, ItemStatus.DELIVERED, ItemStatus.SCRAPPED}
)

# The transition table is the specification. See docs/domain-model.md "Item lifecycle".
_ITEM_TRANSITIONS: Final[dict[ItemStatus, frozenset[ItemStatus]]] = {
    ItemStatus.QUEUED: frozenset({ItemStatus.PREPPING}),
    ItemStatus.PREPPING: frozenset({ItemStatus.IN_OVEN, ItemStatus.SCRAPPED}),
    ItemStatus.IN_OVEN: frozenset({ItemStatus.READY, ItemStatus.SCRAPPED}),
    ItemStatus.READY: frozenset({ItemStatus.DELIVERED}),
}


@dataclass(slots=True)
class OrderItem:
    id: OrderItemId
    order_id: OrderId
    menu_item_id: MenuItemId
    name_snapshot: str
    unit_price_snapshot: Money
    vat_rate_snapshot: VatRate
    prep_seconds: int
    bake_seconds: int
    fire_group: int
    status: ItemStatus = ItemStatus.QUEUED
    priority: Priority = Priority.NORMAL
    remake_of: OrderItemId | None = None
    oven_id: str | None = None
    oven_slot: int | None = None

    @property
    def is_finished(self) -> bool:
        """READY, DELIVERED or SCRAPPED — see invariant #2 in docs/domain-model.md."""
        return self.status in _FINISHED_STATUSES

    def _transition_check(self, target: ItemStatus) -> None:
        if target not in _ITEM_TRANSITIONS.get(self.status, frozenset()):
            msg = f"Item cannot transition {self.status} -> {target}"
            raise IllegalTransitionError(msg)

    def _transition(self, target: ItemStatus) -> None:
        self._transition_check(target)
        self.status = target

    def start_prep(self) -> None:
        self._transition(ItemStatus.PREPPING)

    def check_can_enter_oven(self) -> None:
        """Raises IllegalTransitionError unless `allocate_oven` would be legal
        — lets the application layer refuse before it claims a slot."""
        self._transition_check(ItemStatus.IN_OVEN)

    def allocate_oven(self, oven_id: str, slot: int) -> None:
        """Raises CapacityExceededError if the slot is already occupied. See
        docs/domain-model.md invariant #3 and #4."""
        # Status first: a second `oven-in` on an item already IN_OVEN is an
        # illegal transition (409), not a capacity problem (422).
        self._transition_check(ItemStatus.IN_OVEN)
        if self.oven_id is not None:
            msg = f"Item {self.id} already has an oven slot allocated"
            raise CapacityExceededError(msg)
        self._transition(ItemStatus.IN_OVEN)
        self.oven_id = oven_id
        self.oven_slot = slot

    def mark_ready(self) -> None:
        self._transition(ItemStatus.READY)

    def deliver(self) -> None:
        self._transition(ItemStatus.DELIVERED)

    def scrap(self, reason: str) -> OrderItem:
        """Returns a new `OrderItem` — the remake. `remake_of` points at this
        item, priority is HIGH, and `fire_group`/promised_at are inherited by
        the caller (application layer), never recomputed here. See
        docs/domain-model.md "Item lifecycle" and invariant #6."""
        del reason  # recorded by the caller (metrics label, event payload), not on the item
        self._transition(ItemStatus.SCRAPPED)
        return OrderItem(
            id=OrderItemId(uuid4()),
            order_id=self.order_id,
            menu_item_id=self.menu_item_id,
            name_snapshot=self.name_snapshot,
            unit_price_snapshot=self.unit_price_snapshot,
            vat_rate_snapshot=self.vat_rate_snapshot,
            prep_seconds=self.prep_seconds,
            bake_seconds=self.bake_seconds,
            fire_group=self.fire_group,
            status=ItemStatus.QUEUED,
            priority=Priority.HIGH,
            remake_of=self.id,
        )
