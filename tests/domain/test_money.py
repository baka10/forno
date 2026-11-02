from decimal import Decimal
from uuid import uuid4

import pytest

from forno.domain.ids import MenuItemId, OrderId, OrderItemId
from forno.domain.item import ItemStatus, OrderItem
from forno.domain.money import Currency, CurrencyMismatchError, Money, VatRate
from forno.domain.order import Channel, Order, OrderStatus


def _item(
    price_minor: int = 3200,
    vat: str = "0.08",
    status: ItemStatus = ItemStatus.QUEUED,
) -> OrderItem:
    return OrderItem(
        id=OrderItemId(uuid4()),
        order_id=OrderId(uuid4()),
        menu_item_id=MenuItemId(uuid4()),
        name_snapshot="Margherita",
        unit_price_snapshot=Money(price_minor, Currency.PLN),
        vat_rate_snapshot=VatRate(Decimal(vat)),
        prep_seconds=60,
        bake_seconds=300,
        fire_group=1,
        status=status,
    )


def test_adding_different_currencies_raises():
    pln = Money(100, Currency.PLN)
    eur = Money(100, Currency.EUR)
    with pytest.raises(CurrencyMismatchError):
        pln + eur


def test_subtracting_different_currencies_raises():
    pln = Money(100, Currency.PLN)
    usd = Money(100, Currency.USD)
    with pytest.raises(CurrencyMismatchError):
        pln - usd


def test_money_is_frozen():
    money = Money(100, Currency.PLN)
    with pytest.raises(AttributeError):
        money.minor = 200  # type: ignore[misc]


def test_add_subtract_multiply_stay_in_minor_units():
    a = Money(1000, Currency.PLN)
    b = Money(250, Currency.PLN)
    assert (a + b).minor == 1250
    assert (a - b).minor == 750
    assert (a * 3).minor == 3000


def test_vat_rounded_once_per_line_then_summed():
    """Rounding the order total instead produces off-by-one-cent
    discrepancies against a real till."""
    line = Money(999, Currency.PLN)  # 9.99
    vat = VatRate(Decimal("0.085"))  # 9.99 * 1.085 = 10.83915 -> rounds to 10.84
    gross = line.with_vat(vat)
    assert gross.minor == 1084


def test_vat_applied_independently_per_line_not_on_a_pooled_total():
    line_a = Money(999, Currency.PLN).with_vat(VatRate(Decimal("0.085")))  # -> 1084
    line_b = Money(501, Currency.PLN).with_vat(VatRate(Decimal("0.085")))  # -> 544
    assert line_a.minor == 1084
    assert line_b.minor == 544
    assert (line_a + line_b).minor == 1628


def test_from_decimal_and_to_decimal_round_trip():
    amount = Decimal("32.00")
    money = Money.from_decimal(amount, Currency.PLN)
    assert money.minor == 3200
    assert money.to_decimal() == amount


def test_from_decimal_rounds_half_up():
    money = Money.from_decimal(Decimal("10.005"), Currency.PLN)
    assert money.minor == 1001


def test_total_gross_equals_sum_of_line_totals_over_non_voided_lines():
    """Invariant #5."""
    item_a = _item(price_minor=3200)  # 32.00 * 1.08 = 34.56 -> 3456
    item_b = _item(price_minor=1000)  # 10.00 * 1.08 = 10.80 -> 1080
    order = Order(
        id=OrderId(uuid4()),
        channel=Channel.DINE_IN,
        table_id=None,
        status=OrderStatus.PLACED,
        items=[item_a, item_b],
    )
    assert order.total_gross == Money(3456 + 1080, Currency.PLN)


def test_total_gross_excludes_scrapped_items():
    """A scrapped item's dough was wasted, but the customer is billed once
    via its remake, not twice via the original + remake both counting."""
    scrapped = _item(price_minor=3200, status=ItemStatus.SCRAPPED)
    remake = _item(price_minor=3200, status=ItemStatus.QUEUED)
    order = Order(
        id=OrderId(uuid4()),
        channel=Channel.DINE_IN,
        table_id=None,
        status=OrderStatus.PLACED,
        items=[scrapped, remake],
    )
    assert order.total_gross == Money(3456, Currency.PLN)


def test_total_gross_of_empty_order_is_zero():
    order = Order(
        id=OrderId(uuid4()), channel=Channel.DINE_IN, table_id=None, status=OrderStatus.PLACED
    )
    assert order.total_gross == Money(0, Currency.PLN)


def test_price_change_does_not_alter_placed_order():
    """docs/domain-model.md "Money" — prices are snapshotted at placement.

    An `OrderItem`'s `unit_price_snapshot` has no live reference back to a
    menu item; it's a plain `Money` value fixed at construction, so there
    is no code path in the domain that could retroactively change it.
    """
    item = _item(price_minor=3200)
    original_snapshot = item.unit_price_snapshot

    new_menu_price = Money.from_decimal(Decimal("40.00"), Currency.PLN)

    assert item.unit_price_snapshot is original_snapshot
    assert item.unit_price_snapshot.minor == 3200
    assert new_menu_price.minor != item.unit_price_snapshot.minor
