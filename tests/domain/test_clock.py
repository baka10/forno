from datetime import UTC, datetime

from forno.domain.clock import FrozenClock, SystemClock


def test_system_clock_returns_timezone_aware_utc_now():
    before = datetime.now(UTC)
    now = SystemClock().now()
    after = datetime.now(UTC)
    assert now.tzinfo is not None
    assert before <= now <= after


def test_frozen_clock_always_returns_the_same_instant():
    at = datetime(2026, 7, 22, 18, 0, tzinfo=UTC)
    clock = FrozenClock(at=at)
    assert clock.now() == at
    assert clock.now() == at  # calling twice must not drift


def test_frozen_clock_advance_moves_time_forward_by_exactly_that_much():
    at = datetime(2026, 7, 22, 18, 0, tzinfo=UTC)
    clock = FrozenClock(at=at)
    clock.advance(90)
    assert clock.now() == datetime(2026, 7, 22, 18, 1, 30, tzinfo=UTC)
