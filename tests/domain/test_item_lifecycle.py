from decimal import Decimal
from uuid import uuid4

import pytest

from forno.domain.exceptions import CapacityExceededError, IllegalTransitionError
from forno.domain.ids import MenuItemId, OrderId, OrderItemId
from forno.domain.item import ItemStatus, OrderItem, Priority
from forno.domain.money import Currency, Money, VatRate


def _item(status: ItemStatus = ItemStatus.QUEUED, fire_group: int = 3) -> OrderItem:
    return OrderItem(
        id=OrderItemId(uuid4()),
        order_id=OrderId(uuid4()),
        menu_item_id=MenuItemId(uuid4()),
        name_snapshot="Diavola",
        unit_price_snapshot=Money(3800, Currency.PLN),
        vat_rate_snapshot=VatRate(Decimal("0.08")),
        prep_seconds=90,
        bake_seconds=360,
        fire_group=fire_group,
        status=status,
    )


def test_start_prep_transitions_queued_to_prepping():
    item = _item(status=ItemStatus.QUEUED)
    item.start_prep()
    assert item.status == ItemStatus.PREPPING


def test_mark_ready_transitions_in_oven_to_ready():
    item = _item(status=ItemStatus.IN_OVEN)
    item.mark_ready()
    assert item.status == ItemStatus.READY


def test_is_finished_true_only_for_terminal_statuses():
    for status in (ItemStatus.QUEUED, ItemStatus.PREPPING, ItemStatus.IN_OVEN):
        assert _item(status=status).is_finished is False
    for status in (ItemStatus.READY, ItemStatus.DELIVERED, ItemStatus.SCRAPPED):
        assert _item(status=status).is_finished is True


def test_allocate_oven_transitions_prepping_to_in_oven():
    item = _item(status=ItemStatus.PREPPING)
    item.allocate_oven("deck-1", 3)
    assert item.status == ItemStatus.IN_OVEN
    assert item.oven_id == "deck-1"
    assert item.oven_slot == 3


def test_item_cannot_enter_oven_without_slot_allocation():
    """Invariant #3 — `IN_OVEN` is only reachable through `allocate_oven`,
    which always sets `oven_id`/`oven_slot` in the same call. There is no
    domain path that leaves an item IN_OVEN with no slot recorded."""
    item = _item(status=ItemStatus.QUEUED)
    assert item.oven_id is None
    assert item.oven_slot is None

    with pytest.raises(IllegalTransitionError):
        item.mark_ready()  # QUEUED -> READY is not a legal transition at all
    assert item.status == ItemStatus.QUEUED


def test_oven_slot_allocation_never_exceeds_configured_slot_count():
    """Invariant #4, at the item's own guard: an item already holding an
    oven slot cannot be allocated a second one. (The stronger, system-wide
    guarantee — that the configured *count* of slots is never oversold
    across items — is enforced at the database layer via `oven_slots` rows
    claimed `FOR UPDATE SKIP LOCKED`; see the integration test suite.)"""
    item = _item(status=ItemStatus.PREPPING)
    item.allocate_oven("deck-1", 1)
    # Already IN_OVEN: a second oven-in is an illegal transition (409), not
    # "no capacity" (422) — the chef is repeating a command, not short of ovens.
    with pytest.raises(IllegalTransitionError):
        item.allocate_oven("deck-1", 2)
    # First allocation must be untouched by the rejected second attempt.
    assert item.oven_slot == 1

    # The capacity guard proper: a slot already recorded on an item that is
    # somehow back in PREPPING (never reachable through the FSM, but the
    # guard must hold regardless).
    stale = _item(status=ItemStatus.PREPPING)
    stale.oven_id, stale.oven_slot = "deck-1", 1
    with pytest.raises(CapacityExceededError):
        stale.allocate_oven("deck-1", 2)


def test_deliver_twice_is_rejected():
    """Simulator shadow-ledger invariant #1, unit-tested at the domain
    level too. (The `DeliverItem` *use case* one layer up makes this
    idempotent for callers instead of surfacing the error — see
    application/use_cases/orders.py — but the domain FSM itself must never
    silently allow it, which is what this test pins down.)"""
    item = _item(status=ItemStatus.READY)
    item.deliver()
    assert item.status == ItemStatus.DELIVERED
    with pytest.raises(IllegalTransitionError):
        item.deliver()


@pytest.mark.parametrize("status", [ItemStatus.PREPPING, ItemStatus.IN_OVEN])
def test_scrap_allowed_from_prepping_and_in_oven(status):
    item = _item(status=status)
    remake = item.scrap("burnt")
    assert item.status == ItemStatus.SCRAPPED
    assert remake.status == ItemStatus.QUEUED


@pytest.mark.parametrize("status", [ItemStatus.QUEUED, ItemStatus.READY, ItemStatus.DELIVERED])
def test_scrap_illegal_outside_prepping_or_in_oven(status):
    """A pizza that hasn't started cooking isn't "burnt"; one that's
    already READY can't retroactively become scrapped once the fire-together
    window has already counted it."""
    item = _item(status=status)
    with pytest.raises(IllegalTransitionError):
        item.scrap("burnt")


def test_remake_is_a_new_item_not_a_state_on_the_old_one():
    original = _item(status=ItemStatus.IN_OVEN)
    original_id = original.id

    remake = original.scrap("dropped")

    assert original.status == ItemStatus.SCRAPPED
    assert remake is not original
    assert remake.id != original_id
    assert remake.remake_of == original_id


def test_remake_inherits_original_promise():
    """Invariant #6 — docs/domain-model.md "Item lifecycle".

    `promised_at` lives on the `Order`, not the item, and `scrap()` never
    touches the order — so the remake is implicitly bound to the same
    deadline the original was. What the item itself must get right is the
    `fire_group`, so the remake still fires together with its siblings.
    """
    original = _item(status=ItemStatus.PREPPING, fire_group=5)
    remake = original.scrap("wrong-item")
    assert remake.fire_group == original.fire_group == 5


def test_remake_has_high_priority():
    original = _item(status=ItemStatus.PREPPING)
    assert original.priority == Priority.NORMAL
    remake = original.scrap("burnt")
    assert remake.priority == Priority.HIGH


def test_remake_carries_over_menu_snapshot_not_a_fresh_lookup():
    """The remake is billed and cooked the same as the item it replaces —
    same price, same prep/bake time — not re-priced against current menu
    state."""
    original = _item(status=ItemStatus.IN_OVEN)
    remake = original.scrap("burnt")
    assert remake.unit_price_snapshot == original.unit_price_snapshot
    assert remake.vat_rate_snapshot == original.vat_rate_snapshot
    assert remake.prep_seconds == original.prep_seconds
    assert remake.bake_seconds == original.bake_seconds
    assert remake.menu_item_id == original.menu_item_id
