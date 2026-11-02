from __future__ import annotations

import asyncio

import pytest

from simulator.actors.arrival_generator import ArrivalGenerator
from simulator.clock import ScaledClock
from simulator.models import Channel
from simulator.scenario import ArrivalsConfig, PartiesConfig, RushConfig


@pytest.mark.asyncio
async def test_run_spawns_customers_with_positive_party_sizes() -> None:
    arrivals = ArrivalsConfig(
        model="poisson", per_hour=100000
    )  # fast, so the test doesn't wait long
    parties = PartiesConfig(size_weights={2: 1.0}, takeaway_ratio=0.0)
    generator = ArrivalGenerator(arrivals, parties, master_seed=1)
    clock = ScaledClock(time_scale=1000)

    spawned: list[tuple[int, Channel]] = []

    def spawn_customer(party_size: int, channel: Channel) -> None:
        spawned.append((party_size, channel))

    task = asyncio.create_task(generator.run(spawn_customer, clock))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(spawned) > 0
    assert all(size == 2 for size, _channel in spawned)
    assert all(channel == Channel.DINE_IN for _size, channel in spawned)


@pytest.mark.asyncio
async def test_takeaway_ratio_is_honoured_over_many_arrivals() -> None:
    arrivals = ArrivalsConfig(model="poisson", per_hour=200000)
    parties = PartiesConfig(size_weights={2: 1.0}, takeaway_ratio=1.0)  # always takeaway
    generator = ArrivalGenerator(arrivals, parties, master_seed=1)
    clock = ScaledClock(time_scale=2000)

    spawned: list[Channel] = []
    task = asyncio.create_task(generator.run(lambda size, channel: spawned.append(channel), clock))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(spawned) > 0
    assert all(channel == Channel.TAKEAWAY for channel in spawned)


def test_rush_multiplier_increases_rate_inside_the_window() -> None:
    arrivals = ArrivalsConfig(
        model="poisson",
        per_hour=10,
        rush=RushConfig(at="19:00", duration_minutes=30, multiplier=5),
    )
    parties = PartiesConfig(size_weights={2: 1.0}, takeaway_ratio=0.0)
    generator = ArrivalGenerator(arrivals, parties, master_seed=1)

    # Service is assumed to open at 18:00 (see this plan's Scope Decisions, item 9);
    # 19:00 is 60 minutes in.
    baseline = generator._current_rate_per_hour(elapsed_domain_minutes=0)
    during_rush = generator._current_rate_per_hour(elapsed_domain_minutes=65)
    after_rush = generator._current_rate_per_hour(elapsed_domain_minutes=95)

    assert during_rush == pytest.approx(baseline * 5)
    assert after_rush == pytest.approx(baseline)
