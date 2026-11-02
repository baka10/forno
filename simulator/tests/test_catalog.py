from __future__ import annotations

from decimal import Decimal

from simulator.catalog import ItemIndex, MenuCatalog
from simulator.scenario import MenuItemConfig


def _config(sku: str, popularity: float) -> MenuItemConfig:
    return MenuItemConfig(
        sku=sku,
        name=sku.title(),
        price=Decimal("30.00"),
        vat=Decimal("0.08"),
        prep_seconds=120,
        bake_seconds=480,
        popularity=popularity,
    )


def test_catalog_register_and_choices() -> None:
    catalog = MenuCatalog()
    catalog.register("id-1", _config("margherita", 0.6))
    catalog.register("id-2", _config("diavola", 0.4))

    choices = catalog.choices()

    assert ("id-1", catalog.by_id["id-1"], 0.6) in choices
    assert len(choices) == 2


def test_item_index_looks_up_config_by_order_item_id() -> None:
    catalog = MenuCatalog()
    catalog.register("menu-id-1", _config("margherita", 0.6))
    index = ItemIndex()

    index.register_order(
        order_item_ids=["order-item-1", "order-item-2"],
        menu_item_ids=["menu-id-1", "menu-id-1"],
        catalog=catalog,
    )

    found = index.lookup("order-item-1")
    assert found is not None
    assert found.sku == "margherita"
    assert index.lookup("unknown") is None
