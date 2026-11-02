"""A running tally of what the simulator observed, built exclusively from
what an external client sees: requests it sent, responses it received, SSE
events it consumed. Never reads the database. See docs/simulator.md "The
shadow ledger".

This implements a deliberately reduced subset of the 10 invariants
docs/simulator.md sketches — the table there says which ones are asserted,
which are not, and why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from simulator.scenario import ExpectConfig


@dataclass(slots=True)
class InvariantResult:
    number: int
    description: str
    passed: bool
    detail: str = ""


@dataclass(slots=True)
class ShadowLedger:
    """Populated by every actor as requests/responses/events are observed —
    see docs/simulator.md "Actors" (the dotted lines to `LEDGER`)."""

    requests: list[tuple[str, str, int]] = field(default_factory=list)
    sse_events: list[tuple[str, str]] = field(default_factory=list)

    orders_placed: int = 0
    orders_served: int = 0
    orders_cancelled: int = 0
    orders_voided: int = 0
    walkouts: int = 0
    items_ordered: int = 0
    remakes: int = 0
    scrapped: int = 0
    promise_deltas_seconds: list[float] = field(default_factory=list)
    delivered_item_ids: set[str] = field(default_factory=set)
    duplicate_deliveries: int = 0

    def record_request(self, method: str, path: str, status_code: int) -> None:
        self.requests.append((method, path, status_code))

    def record_sse_event(self, stream: str, event_type: str, payload: dict[str, Any]) -> None:
        del payload  # only the type is tallied — see the reduced-scope note above
        self.sse_events.append((stream, event_type))

    def record_order_placed(self, item_count: int) -> None:
        self.orders_placed += 1
        self.items_ordered += item_count

    def record_order_served(self) -> None:
        self.orders_served += 1

    def record_promise_delta(self, domain_seconds: float) -> None:
        """Ready time minus promised time, in domain seconds: negative is
        early, positive is late. Recorded once per order, including for
        orders the customer gave up on (a lower bound on how late)."""
        self.promise_deltas_seconds.append(domain_seconds)

    def record_order_cancelled(self) -> None:
        self.orders_cancelled += 1

    def record_order_voided(self) -> None:
        self.orders_voided += 1

    def record_walkout(self) -> None:
        self.walkouts += 1

    def record_remake(self) -> None:
        self.remakes += 1

    def record_scrap(self) -> None:
        self.scrapped += 1

    def record_delivery(self, item_id: str) -> None:
        if item_id in self.delivered_item_ids:
            self.duplicate_deliveries += 1
        else:
            self.delivered_item_ids.add(item_id)

    def check_invariants(self, expect: ExpectConfig) -> list[InvariantResult]:
        results = [self._invariant_1(), self._invariant_6()]
        if self.promise_deltas_seconds:
            results.append(self._invariant_8(expect))
        results.append(self._walkouts_within_ceiling(expect))
        return results

    def _invariant_1(self) -> InvariantResult:
        return InvariantResult(
            number=1,
            description="No item observed DELIVERED more than once",
            passed=self.duplicate_deliveries == 0,
            detail=f"{self.duplicate_deliveries} duplicate deliveries observed",
        )

    def _invariant_6(self) -> InvariantResult:
        produced = len(self.delivered_item_ids)
        expected_ceiling = self.items_ordered + self.remakes - self.scrapped
        return InvariantResult(
            number=6,
            description="produced <= ordered + remakes - scrapped",
            passed=produced <= expected_ceiling,
            detail=(
                f"produced={produced} ordered={self.items_ordered} "
                f"remakes={self.remakes} scrapped={self.scrapped}"
            ),
        )

    def _invariant_8(self, expect: ExpectConfig) -> InvariantResult:
        breaches = sum(1 for delta in self.promise_deltas_seconds if delta > 0)
        breach_rate = breaches / len(self.promise_deltas_seconds)
        band = expect.breach_rate
        return InvariantResult(
            number=8,
            description="Promise-breach rate falls within the scenario's declared band",
            passed=band.min <= breach_rate <= band.max,
            detail=f"breach_rate={breach_rate:.1%} band=[{band.min:.0%}, {band.max:.0%}]",
        )

    def _walkouts_within_ceiling(self, expect: ExpectConfig) -> InvariantResult:
        return InvariantResult(
            number=99,
            description="Walkouts stay within the scenario's declared ceiling",
            passed=self.walkouts <= expect.walkouts_max,
            detail=f"walkouts={self.walkouts} max={expect.walkouts_max}",
        )
