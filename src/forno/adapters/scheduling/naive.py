"""The default `PromiseEstimator`. See docs/domain-model.md "Promised time".

Deliberately unimpressive — the arithmetic a manager does in their head —
but physically honest: an order cannot be ready before the pizzas ahead of
it have been prepped, its own slowest pizza has been prepped *and* baked,
and, if it outnumbers the free oven slots, extra oven rounds have run.
Ignores `fire_group`, priority and per-slot release times — present because
a constraint solver needs them (docs/adr/0007-cp-sat-promise-estimator-seam.md).

    eta = (backlog prep + own prep) / chefs     # every pizza ahead of, and in, this order
        + longest bake                          # the last pizza's time in the oven
        + extra oven rounds * mean bake         # when the order outnumbers free slots
        + margin                                # pick-up, pass, walk to the table
    capped at 20 minutes                        # nobody quotes more and keeps the table

The cap is the naive part that matters: an honest forty-minute quote would
send the customer away and never be "late"; a manager quotes twenty, keeps
the order, and the SLO records what actually happened.
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import TYPE_CHECKING

from forno.domain.ports import PromisedTime, PromiseEstimator

if TYPE_CHECKING:
    from forno.domain.kitchen_state import KitchenState
    from forno.domain.order import Order

_MARGIN_SECONDS = 180
_MAX_PROMISE_SECONDS = 20 * 60
_PREP_STATION = "prep"


class NaiveEstimator(PromiseEstimator):
    def promise(self, order: Order, state: KitchenState) -> PromisedTime:
        items = order.items
        if not items:
            return PromisedTime(
                at=state.now + timedelta(seconds=_MARGIN_SECONDS / state.time_scale)
            )

        chefs = max(
            (s.capacity for s in state.stations if s.station_id == _PREP_STATION), default=1
        )
        # Chefs prep one pizza at a time: the pizzas already waiting and this
        # order's own pizzas all go through the same pairs of hands.
        backlog_prep = sum(item.prep_seconds for item in state.queued)
        own_prep = sum(item.prep_seconds for item in items)
        prep_wait = (backlog_prep + own_prep) / max(chefs, 1)

        longest_bake = max(item.bake_seconds for item in items)
        mean_bake = sum(item.bake_seconds for item in items) / len(items)

        free_slots = sum(
            1
            for oven in state.ovens
            for slot in oven.slots
            if slot.release_time is None or slot.release_time <= state.now
        )
        free_slots = max(free_slots, 1)
        extra_rounds = math.ceil(len(items) / free_slots) - 1

        eta_domain_seconds = min(
            prep_wait + longest_bake + extra_rounds * mean_bake + _MARGIN_SECONDS,
            _MAX_PROMISE_SECONDS,
        )
        return PromisedTime(at=state.now + timedelta(seconds=eta_domain_seconds / state.time_scale))
