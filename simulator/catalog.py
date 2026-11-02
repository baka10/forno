"""In-process lookup tables shared between actors within one simulator run.
Not persisted, not sent over the wire — pure convenience so `ChefActor` knows
an item's real prep/bake seconds without an extra `GET` round trip, and
`CustomerActor` knows which server-assigned menu item ids exist to order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simulator.scenario import MenuItemConfig


@dataclass(slots=True)
class MenuCatalog:
    """`menu_item_id` (server-assigned) -> the config it was created from."""

    by_id: dict[str, MenuItemConfig] = field(default_factory=dict)
    _order: list[str] = field(default_factory=list)

    def register(self, menu_item_id: str, config: MenuItemConfig) -> None:
        self.by_id[menu_item_id] = config
        self._order.append(menu_item_id)

    def choices(self) -> list[tuple[str, MenuItemConfig, float]]:
        return [
            (item_id, self.by_id[item_id], self.by_id[item_id].popularity)
            for item_id in self._order
        ]


@dataclass(slots=True)
class ItemIndex:
    """`order_item_id` -> the `MenuItemConfig` backing it. Populated by
    `CashierActor` right after a successful `PlaceOrder`."""

    by_order_item_id: dict[str, MenuItemConfig] = field(default_factory=dict)

    def register_order(
        self, order_item_ids: list[str], menu_item_ids: list[str], catalog: MenuCatalog
    ) -> None:
        for order_item_id, menu_item_id in zip(order_item_ids, menu_item_ids, strict=True):
            config = catalog.by_id.get(menu_item_id)
            if config is not None:
                self.by_order_item_id[order_item_id] = config

    def lookup(self, order_item_id: str) -> MenuItemConfig | None:
        return self.by_order_item_id.get(order_item_id)
