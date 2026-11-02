from __future__ import annotations

from simulator.scenario import ExpectConfig, ExpectRange
from simulator.shadow_ledger import ShadowLedger


def _expect(
    min_breach: float = 0.0, max_breach: float = 1.0, walkouts_max: int = 100
) -> ExpectConfig:
    return ExpectConfig(
        breach_rate=ExpectRange(min=min_breach, max=max_breach), walkouts_max=walkouts_max
    )


def test_no_duplicate_deliveries_passes_by_default() -> None:
    ledger = ShadowLedger()
    ledger.record_delivery("item-1")

    results = ledger.check_invariants(_expect())

    invariant_1 = next(r for r in results if r.number == 1)
    assert invariant_1.passed


def test_duplicate_delivery_fails_invariant_1() -> None:
    ledger = ShadowLedger()
    ledger.record_delivery("item-1")
    ledger.record_delivery("item-1")

    results = ledger.check_invariants(_expect())

    invariant_1 = next(r for r in results if r.number == 1)
    assert not invariant_1.passed


def test_production_never_exceeds_ordered_plus_remakes_minus_scrapped() -> None:
    ledger = ShadowLedger()
    ledger.record_order_placed(item_count=2)
    ledger.record_delivery("a")
    ledger.record_delivery("b")
    ledger.record_delivery("c")  # a 3rd delivery with nothing ordered/remade to back it

    results = ledger.check_invariants(_expect())

    invariant_6 = next(r for r in results if r.number == 6)
    assert not invariant_6.passed


def test_breach_rate_within_band_passes() -> None:
    ledger = ShadowLedger()
    ledger.record_promise_delta(5.0)  # breach: ready 5 domain seconds late
    ledger.record_promise_delta(-5.0)  # on time: ready 5 domain seconds early

    results = ledger.check_invariants(_expect(min_breach=0.3, max_breach=0.7))

    invariant_8 = next(r for r in results if r.number == 8)
    assert invariant_8.passed  # 1/2 = 50%, inside [30%, 70%]


def test_walkouts_over_ceiling_fails() -> None:
    ledger = ShadowLedger()
    ledger.record_walkout()
    ledger.record_walkout()

    results = ledger.check_invariants(_expect(walkouts_max=1))

    walkout_check = next(r for r in results if r.number == 99)
    assert not walkout_check.passed
