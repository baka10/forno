"""Shared fixtures. `FrozenClock` for anything time-dependent — a test that
calls `datetime.now()` fails at midnight on a Tuesday in a different
timezone. See CONTRIBUTING.md "Testing strategy".
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from forno.domain.clock import FrozenClock


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock(at=datetime(2026, 7, 22, 18, 0, tzinfo=UTC))


@pytest.fixture
def in_memory_publisher():
    from forno.adapters.messaging.publisher import InMemoryPublisher

    return InMemoryPublisher()
