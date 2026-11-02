from __future__ import annotations

import time

import pytest

from simulator.clock import ScaledClock


@pytest.mark.asyncio
async def test_sleep_domain_seconds_divides_by_time_scale() -> None:
    clock = ScaledClock(time_scale=100)
    start = time.monotonic()
    await clock.sleep_domain_seconds(10)  # 10 domain seconds / 100x = 0.1s wall clock
    elapsed = time.monotonic() - start

    assert 0.05 < elapsed < 0.5


@pytest.mark.asyncio
async def test_real_time_scale_is_a_noop_divisor() -> None:
    clock = ScaledClock(time_scale=1)
    start = time.monotonic()
    await clock.sleep_domain_seconds(0.05)
    elapsed = time.monotonic() - start

    assert elapsed >= 0.04


def test_time_scale_is_exposed() -> None:
    clock = ScaledClock(time_scale=60)
    assert clock.time_scale == 60
