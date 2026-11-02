from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from forno.adapters.scheduling.naive import NaiveEstimator
from forno.domain.ids import MenuItemId, OrderId, OrderItemId, OvenId, StationId
from forno.domain.item import OrderItem, Priority
from forno.domain.kitchen_state import (
    KitchenState,
    OvenSlotState,
    OvenState,
    QueuedItem,
    StationState,
)
from forno.domain.money import Currency, Money, VatRate
from forno.domain.order import Channel, Order, OrderStatus

_NOW = datetime(2026, 7, 22, 18, 0, tzinfo=UTC)
_MARGIN = 180


def _item(bake_seconds: int, prep_seconds: int = 60) -> OrderItem:
    return OrderItem(
        id=OrderItemId(uuid4()),
        order_id=OrderId(uuid4()),
        menu_item_id=MenuItemId(uuid4()),
        name_snapshot="Margherita",
        unit_price_snapshot=Money(3200, Currency.PLN),
        vat_rate_snapshot=VatRate(Decimal("0.08")),
        prep_seconds=prep_seconds,
        bake_seconds=bake_seconds,
        fire_group=1,
    )


def _order(*items: OrderItem) -> Order:
    return Order(
        id=OrderId(uuid4()),
        channel=Channel.DINE_IN,
        table_id=None,
        status=OrderStatus.PLACED,
        items=list(items),
    )


def _oven(free_slots: int, occupied_slots: int = 0, bake_seconds: int = 300) -> OvenState:
    slots = tuple(OvenSlotState(slot=i, release_time=None) for i in range(free_slots))
    slots += tuple(
        OvenSlotState(slot=free_slots + i, release_time=_NOW + timedelta(seconds=600))
        for i in range(occupied_slots)
    )
    return OvenState(oven_id=OvenId("deck-1"), bake_seconds=bake_seconds, slots=slots)


def _queued(count: int, prep_seconds: int = 60) -> tuple[QueuedItem, ...]:
    return tuple(
        QueuedItem(
            item_id=OrderItemId(uuid4()),
            prep_seconds=prep_seconds,
            bake_seconds=300,
            fire_group=1,
            priority=Priority.NORMAL,
        )
        for _ in range(count)
    )


def _prep(chefs: int) -> StationState:
    return StationState(station_id=StationId("prep"), capacity=chefs, current_claims=0)


def _state(
    *ovens: OvenState,
    time_scale: int = 1,
    chefs: int | None = None,
    queued: tuple[QueuedItem, ...] = (),
) -> KitchenState:
    stations = (_prep(chefs),) if chefs is not None else ()
    return KitchenState(
        now=_NOW, ovens=ovens, stations=stations, queued=queued, time_scale=time_scale
    )


def test_one_chef_preps_the_orders_pizzas_one_after_another_before_the_last_bake():
    """Two pizzas, one chef, plenty of oven: 60s + 120s of prep in sequence,
    then the last one's 300s bake, then the margin."""
    order = _order(_item(300, prep_seconds=60), _item(300, prep_seconds=120))
    promise = NaiveEstimator().promise(order, _state(_oven(free_slots=6), chefs=1))
    assert promise.at == _NOW + timedelta(seconds=(60 + 120) + 300 + _MARGIN)


def test_more_chefs_share_the_orders_prep():
    order = _order(_item(300, prep_seconds=60), _item(300, prep_seconds=120))
    promise = NaiveEstimator().promise(order, _state(_oven(free_slots=6), chefs=3))
    assert promise.at == _NOW + timedelta(seconds=(60 + 120) / 3 + 300 + _MARGIN)


def test_order_outnumbering_free_slots_is_promised_an_extra_oven_round_per_overflow():
    order = _order(_item(300), _item(300), _item(300))  # 3 pizzas, 1 free slot -> 2 extra rounds
    promise = NaiveEstimator().promise(
        order, _state(_oven(free_slots=1, occupied_slots=5), chefs=3)
    )
    assert promise.at == _NOW + timedelta(seconds=180 / 3 + 300 + 2 * 300 + _MARGIN)


def test_more_free_slots_means_an_earlier_promise():
    order = _order(_item(300), _item(300), _item(300))
    slower = NaiveEstimator().promise(order, _state(_oven(free_slots=1, occupied_slots=5)))
    faster = NaiveEstimator().promise(order, _state(_oven(free_slots=3, occupied_slots=3)))
    assert faster.at < slower.at


def test_promise_with_zero_free_slots_does_not_divide_by_zero():
    order = _order(_item(300))
    # Must not raise ZeroDivisionError — falls back to treating capacity as 1.
    promise = NaiveEstimator().promise(order, _state(_oven(free_slots=0, occupied_slots=6)))
    assert promise.at == _NOW + timedelta(seconds=60 + 300 + _MARGIN)


def test_promise_with_no_ovens_or_stations_at_all_still_returns_a_time():
    order = _order(_item(300))
    promise = NaiveEstimator().promise(order, _state())
    assert promise.at == _NOW + timedelta(seconds=60 + 300 + _MARGIN)


def test_promise_treats_a_slot_with_a_past_release_time_as_free():
    order = _order(_item(300), _item(300))
    slot_freeing_soon = OvenState(
        oven_id=OvenId("deck-1"),
        bake_seconds=300,
        slots=(
            OvenSlotState(slot=1, release_time=_NOW - timedelta(seconds=1)),
            OvenSlotState(slot=2, release_time=_NOW - timedelta(seconds=1)),
        ),
    )
    promise = NaiveEstimator().promise(order, _state(slot_freeing_soon, chefs=2))
    # Two free slots, two pizzas, two chefs: no extra round, prep in parallel.
    assert promise.at == _NOW + timedelta(seconds=120 / 2 + 300 + _MARGIN)


def test_backlog_ahead_of_the_order_pushes_the_promise_out_by_prep_time_per_chef():
    """Six pizzas of 60s prep already waiting and two chefs on shift: this
    order's own pizza joins that queue."""
    order = _order(_item(300))
    promise = NaiveEstimator().promise(
        order, _state(_oven(free_slots=6), chefs=2, queued=_queued(6))
    )
    assert promise.at == _NOW + timedelta(seconds=(6 * 60 + 60) / 2 + 300 + _MARGIN)


def test_promise_is_capped_at_twenty_minutes_however_deep_the_backlog():
    """The deliberately naive part: a manager never quotes more than twenty
    minutes. An honest forty-minute quote would never be 'late' — the
    customer would just leave — so the cap is what lets the SLO see a bad
    night."""
    order = _order(_item(300))
    promise = NaiveEstimator().promise(
        order, _state(_oven(free_slots=1), chefs=1, queued=_queued(30, prep_seconds=240))
    )
    assert promise.at == _NOW + timedelta(minutes=20)


def test_promise_is_shortened_by_the_time_scale_but_stays_a_wall_clock_instant():
    """ADR-0009: durations are domain seconds, `promised_at` is wall-clock.
    At 120x a 540s domain ETA lands 4.5 wall seconds from now."""
    order = _order(_item(300))
    promise = NaiveEstimator().promise(order, _state(_oven(free_slots=2), time_scale=120))
    assert promise.at == _NOW + timedelta(seconds=(60 + 300 + _MARGIN) / 120)


def test_promise_ignores_fire_group_by_design():
    """Documented, deliberate limitation — see adapters/scheduling/naive.py.
    Two orders that differ only in fire_group get the same promise."""
    order_a = _order(_item(300))
    order_a.items[0].fire_group = 1
    order_b = _order(_item(300))
    order_b.items[0].fire_group = 99
    state = _state(_oven(free_slots=2))
    assert (
        NaiveEstimator().promise(order_a, state).at == NaiveEstimator().promise(order_b, state).at
    )
