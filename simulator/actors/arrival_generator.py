"""Poisson arrivals with a configurable rush multiplier; spawns customers
with a party-size distribution. See docs/simulator.md "Actors".

Each actor derives its own `random.Random` from the master seed via stable
name-based derivation, so adding a second waiter never reshuffles the
customer arrival stream. See docs/simulator.md "Determinism".
"""

from __future__ import annotations

import hashlib
from random import Random
from typing import TYPE_CHECKING

from simulator.models import Channel

if TYPE_CHECKING:
    from collections.abc import Callable

    from simulator.clock import ScaledClock
    from simulator.scenario import ArrivalsConfig, PartiesConfig

# Dinner service is assumed to open at 18:00. `rush.at` in every bundled
# scenario is a clock-of-day string with no declared anchor; 18:00 places
# busy-friday's 19:00 rush roughly mid-run.
_SERVICE_START_MINUTES = 18 * 60
_MIN_RATE_PER_HOUR = 0.1


def rng_for(name: str, master_seed: int) -> Random:
    seed_material = f"{name}:{master_seed}".encode()
    digest = hashlib.blake2b(seed_material, digest_size=8).hexdigest()
    return Random(int(digest, 16))


def _parse_hhmm_to_minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


class ArrivalGenerator:
    def __init__(self, arrivals: ArrivalsConfig, parties: PartiesConfig, master_seed: int) -> None:
        self._arrivals = arrivals
        self._parties = parties
        self._rng = rng_for("arrival_generator", master_seed)

    async def run(self, spawn_customer: Callable[[int, Channel], None], clock: ScaledClock) -> None:
        elapsed_minutes = 0.0
        while True:
            rate = self._current_rate_per_hour(elapsed_minutes)
            interval_seconds = self._rng.expovariate(rate / 3600.0)
            await clock.sleep_domain_seconds(interval_seconds)
            elapsed_minutes += interval_seconds / 60.0

            party_size = self._pick_party_size()
            channel = (
                Channel.TAKEAWAY
                if self._rng.random() < self._parties.takeaway_ratio
                else Channel.DINE_IN
            )
            spawn_customer(party_size, channel)

    def _current_rate_per_hour(self, elapsed_domain_minutes: float) -> float:
        rate = self._arrivals.per_hour
        rush = self._arrivals.rush
        if rush is not None:
            clock_of_day_minutes = _SERVICE_START_MINUTES + elapsed_domain_minutes
            rush_start = _parse_hhmm_to_minutes(rush.at)
            if rush_start <= clock_of_day_minutes < rush_start + rush.duration_minutes:
                rate *= rush.multiplier
        return max(rate, _MIN_RATE_PER_HOUR)

    def _pick_party_size(self) -> int:
        sizes = list(self._parties.size_weights.keys())
        weights = list(self._parties.size_weights.values())
        return self._rng.choices(sizes, weights=weights, k=1)[0]
