"""Unimplemented, fully specified: docs/adr/0007-cp-sat-promise-estimator-seam.md.

The model in full (see the ADR for constraint definitions):

    Decision variables : start_prep[i], start_bake[i], slot[i]
    Constraints        : precedence, prep capacity, oven capacity,
                          slot availability, fire-together window,
                          remake priority
    Objective          : lexicographic — breach, then fire-group spread,
                          then makespan
    Time budget        : 150ms hard limit; best feasible on timeout;
                          NaiveEstimator on infeasible/no solution
    Determinism        : fixed worker count and random seed

Requires the `scheduling` extra (`ortools`), which stays optional — this
module must not be imported unconditionally by anything in the default
install path. Registering it under `FORNO_PROMISE_ESTIMATOR=cpsat` is what
would move this ADR from Proposed to Accepted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forno.domain.ports import PromisedTime, PromiseEstimator

if TYPE_CHECKING:
    from forno.domain.kitchen_state import KitchenState
    from forno.domain.order import Order


class CpSatEstimator(PromiseEstimator):
    def __init__(self, time_budget_ms: int = 150) -> None:
        raise NotImplementedError

    def promise(self, order: Order, state: KitchenState) -> PromisedTime:
        raise NotImplementedError
