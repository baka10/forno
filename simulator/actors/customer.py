"""Deliberates, picks items (weighted by menu popularity), waits, walks out
if the promise is badly broken. See docs/simulator.md "Actors".

Owns the whole per-visit flow (seat -> deliberate -> order -> wait -> pay/
leave -> clear) rather than a narrower decide/wait split: a customer that
only decides has no way to actually place an order or poll its status, and
the cashier/waiter would have to carry per-customer state instead.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx

from simulator.log import get_logger

if TYPE_CHECKING:
    from random import Random

    from simulator.actors.cashier import CashierActor
    from simulator.catalog import MenuCatalog
    from simulator.clock import ScaledClock
    from simulator.http_client import FornoClient
    from simulator.models import Channel
    from simulator.scenario import BehaviourConfig
    from simulator.shadow_ledger import ShadowLedger

logger = get_logger(__name__)

# Domain seconds between glances at the order status — a guest looks towards
# the kitchen every half minute or so. Domain-scaled like every other wait, so
# walkout timing stays accurate at any time_scale.
_POLL_DOMAIN_SECONDS = 30.0


class CustomerActor:
    def __init__(  # noqa: PLR0913 -- explicit dependency injection of collaborators
        self,
        cashier: CashierActor,
        waiter_client: FornoClient,
        order_status_client: FornoClient,
        clock: ScaledClock,
        catalog: MenuCatalog,
        ledger: ShadowLedger,
        behaviour: BehaviourConfig,
        rng: Random,
    ) -> None:
        self._cashier = cashier
        self._waiter_client = waiter_client
        self._order_status_client = order_status_client
        self._clock = clock
        self._catalog = catalog
        self._ledger = ledger
        self._behaviour = behaviour
        self._rng = rng

    def decide_order(self, party_size: int) -> list[dict[str, Any]]:
        """Weighted by `popularity`. Returns order lines for the cashier —
        one pizza per diner, all in one fire_group (pizza arrives as a single
        course)."""
        choices = self._catalog.choices()
        ids = [item_id for item_id, _config, _weight in choices]
        weights = [weight for _id, _config, weight in choices]
        picks = self._rng.choices(ids, weights=weights, k=party_size)

        counts: dict[str, int] = {}
        for item_id in picks:
            counts[item_id] = counts.get(item_id, 0) + 1
        return [
            {"menu_item_id": item_id, "quantity": quantity, "fire_group": 1}
            for item_id, quantity in counts.items()
        ]

    def should_walk_out(self, elapsed_seconds: float) -> bool:
        """Returns True once the wait has crossed the configured threshold."""
        return elapsed_seconds >= self._behaviour.walkout_after_seconds

    async def visit(self, party_size: int, channel: Channel, table_number: int | None) -> None:
        if table_number is not None:
            resp = await self._waiter_client.post(
                f"/tables/{table_number}/seat", {"party_size": party_size}
            )
            self._ledger.record_request("POST", "/tables/{number}/seat", resp.status_code)
            logger.info("customer seated", table=table_number, party_size=party_size)

        deliberation = self._rng.uniform(
            self._behaviour.deliberation_seconds.min, self._behaviour.deliberation_seconds.max
        )
        await self._clock.sleep_domain_seconds(deliberation)

        lines = self.decide_order(party_size)
        if not lines:
            await self._leave(table_number)
            return

        order = await self._cashier.place_order(channel.value, table_number, lines)
        if order is None:
            await self._leave(table_number)
            return

        served = await self._wait_for_service(order)
        if served:
            await self._cashier.close_order(order["id"])
        else:
            logger.info("customer walked out", order_id=order["id"], table=table_number)
            self._ledger.record_walkout()
            await self._cashier.cancel_or_escalate(order["id"])
        await self._leave(table_number)

    async def _wait_for_service(self, order: dict[str, Any]) -> bool:
        """Polls the order until SERVED or the walkout threshold. Scores the
        promise on the way — as domain seconds early (negative) or late
        (positive) — the first time the order is seen READY or SERVED; a
        customer who walks out before the food is ready scores it late by at
        least the wait they gave up on, which is what a real complaint says."""
        placed_at = datetime.fromisoformat(order["placed_at"])
        promised_at = datetime.fromisoformat(order["promised_at"])
        promised_offset = (promised_at - placed_at).total_seconds() * self._clock.time_scale
        scored = False
        elapsed = 0.0
        while not self.should_walk_out(elapsed):
            resp = await self._order_status_client.get(f"/orders/{order['id']}")
            self._ledger.record_request("GET", "/orders/{id}", resp.status_code)
            if resp.status_code == httpx.codes.OK:
                body = resp.json()
                if not scored and body.get("ready_at"):
                    ready_at = datetime.fromisoformat(body["ready_at"])
                    self._ledger.record_promise_delta(
                        (ready_at - promised_at).total_seconds() * self._clock.time_scale
                    )
                    scored = True
                if body["status"] == "SERVED":
                    self._ledger.record_order_served()
                    return True
            await self._clock.sleep_domain_seconds(_POLL_DOMAIN_SECONDS)
            elapsed += _POLL_DOMAIN_SECONDS
        if not scored:
            self._ledger.record_promise_delta(max(elapsed - promised_offset, 0.0))
        return False

    async def _leave(self, table_number: int | None) -> None:
        if table_number is not None:
            resp = await self._waiter_client.post(f"/tables/{table_number}/clear", {})
            self._ledger.record_request("POST", "/tables/{number}/clear", resp.status_code)
