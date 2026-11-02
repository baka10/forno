"""Polls `GET /kitchen/tickets?station=prep` to discover newly QUEUED items
(the kitchen SSE stream never carries the initial QUEUED state — a ticket
line is born QUEUED by the projector, and the first `ItemStateChanged` is
PREPPING), claims items by station, respects oven capacity via retry-on-422,
occasionally scraps at `remake_rate`. See docs/simulator.md "Actors".

A chef has two hands: items are prepped one at a time, oldest ticket first,
and a chef holding a pizza the oven has no room for waits with it. Once a
pizza is in the oven it bakes on its own — the chef moves on to the next
item and comes back to pull it out when the timer rings. Prep capacity is
therefore `chefs / prep_seconds`, and the oven is the other constraint.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import TYPE_CHECKING

import httpx

from simulator.log import get_logger

if TYPE_CHECKING:
    from random import Random

    from simulator.catalog import ItemIndex
    from simulator.clock import ScaledClock
    from simulator.http_client import FornoClient
    from simulator.shadow_ledger import ShadowLedger

logger = get_logger(__name__)

# Domain seconds between ticket-board scans when there is nothing to do — a
# chef glances at the board every ~20s. Domain-scaled via the clock so
# discovery latency is realistic at any time_scale.
_POLL_DOMAIN_SECONDS = 20.0
# Oven-retry waits are *domain* seconds (scaled by the clock), not wall-clock:
# at 120x a full bake is ~4s wall, so a raw multi-second wall sleep here would
# idle a freed oven slot for domain-minutes and collapse kitchen throughput.
# In domain terms a chef re-checks a full oven every ~15-45s, backing off.
_OVEN_RETRY_MIN_DOMAIN_SECONDS = 15.0
_OVEN_RETRY_MAX_DOMAIN_SECONDS = 45.0
_OVEN_RETRY_BACKOFF_CAP_DOMAIN_SECONDS = 180.0
_FALLBACK_PREP_SECONDS = 120
_FALLBACK_BAKE_SECONDS = 480
_SCRAP_REASONS = ("burnt", "dropped", "wrong-item")


class ChefActor:
    def __init__(  # noqa: PLR0913 -- explicit dependency injection of collaborators
        self,
        client: FornoClient,
        ledger: ShadowLedger,
        item_index: ItemIndex,
        remake_rate: float,
        clock: ScaledClock,
        rng: Random,
    ) -> None:
        self._client = client
        self._ledger = ledger
        self._item_index = item_index
        self._remake_rate = remake_rate
        self._clock = clock
        self._rng = rng
        self._backlog: deque[tuple[str, str]] = deque()
        self._seen: set[str] = set()
        self._background_tasks: set[asyncio.Task[None]] = set()

    async def run(self) -> None:
        """Scan the board, work the backlog one item at a time, repeat.
        Every chef scans the same board; the server's `start` hands each
        item to exactly one of them (the others get a 409 and move on)."""
        while True:
            await self._scan_board()
            if not self._backlog:
                await self._clock.sleep_domain_seconds(_POLL_DOMAIN_SECONDS)
                continue
            ticket_id, item_id = self._backlog.popleft()
            await self._work_item(ticket_id, item_id)

    async def _scan_board(self) -> None:
        response = await self._client.get("/kitchen/tickets", params={"station": "prep"})
        self._ledger.record_request("GET", "/kitchen/tickets", response.status_code)
        if response.status_code != httpx.codes.OK:
            return
        for ticket in response.json():
            for line in ticket["lines"]:
                item_id = line["order_item_id"]
                if line["status"] != "QUEUED" or item_id in self._seen:
                    continue
                self._seen.add(item_id)
                self._backlog.append((ticket["id"], item_id))

    async def _work_item(self, ticket_id: str, item_id: str) -> None:
        """Prep in the chef's hands; the bake continues in the background."""
        start = await self._client.post(f"/kitchen/tickets/{ticket_id}/items/{item_id}/start", {})
        self._ledger.record_request(
            "POST", "/kitchen/tickets/{tid}/items/{iid}/start", start.status_code
        )
        if start.status_code != httpx.codes.OK:
            return  # another chef got there first, or the order was cancelled underneath us
        logger.info("chef started item", item_id=item_id, ticket_id=ticket_id)

        config = self._item_index.lookup(item_id)
        prep_seconds = config.prep_seconds if config is not None else _FALLBACK_PREP_SECONDS
        bake_seconds = config.bake_seconds if config is not None else _FALLBACK_BAKE_SECONDS

        await self._clock.sleep_domain_seconds(prep_seconds)
        if self._rng.random() < self._remake_rate:
            await self._scrap(ticket_id, item_id)
            return

        if not await self._oven_in_with_retry(ticket_id, item_id):
            return  # the order is no longer being worked (cancelled/voided)
        logger.info("chef put item in oven", item_id=item_id, ticket_id=ticket_id)
        task = asyncio.create_task(self._bake(ticket_id, item_id, bake_seconds))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _bake(self, ticket_id: str, item_id: str, bake_seconds: int) -> None:
        await self._clock.sleep_domain_seconds(bake_seconds)
        if self._rng.random() < self._remake_rate:
            await self._scrap(ticket_id, item_id)
            return
        try:
            ready = await self._client.post(
                f"/kitchen/tickets/{ticket_id}/items/{item_id}/ready", {}
            )
        except httpx.HTTPError:
            return  # the run is shutting down and the client is gone; the oven timer is moot
        self._ledger.record_request(
            "POST", "/kitchen/tickets/{tid}/items/{iid}/ready", ready.status_code
        )
        if ready.status_code == httpx.codes.OK:
            logger.info("chef marked item ready", item_id=item_id, ticket_id=ticket_id)

    async def _oven_in_with_retry(
        self,
        ticket_id: str,
        item_id: str,
        min_domain_seconds: float = _OVEN_RETRY_MIN_DOMAIN_SECONDS,
        max_domain_seconds: float = _OVEN_RETRY_MAX_DOMAIN_SECONDS,
    ) -> bool:
        """Jittered, backing-off retry on 422 (no free oven slot) — a chef
        checks back periodically, not in a tight loop, and checks less often
        the longer the oven stays full. Waits are domain-scaled via the clock
        so the cadence is sane at any `time_scale` (see the constants above).
        Returns True once the item is in the oven, False if the server
        refused for any other reason (the order was cancelled meanwhile)."""
        wait_domain_seconds = self._rng.uniform(min_domain_seconds, max_domain_seconds)
        while True:
            response = await self._client.post(
                f"/kitchen/tickets/{ticket_id}/items/{item_id}/oven-in", {}
            )
            self._ledger.record_request(
                "POST", "/kitchen/tickets/{tid}/items/{iid}/oven-in", response.status_code
            )
            if response.status_code != httpx.codes.UNPROCESSABLE_ENTITY:
                return response.status_code == httpx.codes.OK
            # no oven slot free yet — check back later
            await self._clock.sleep_domain_seconds(wait_domain_seconds)
            wait_domain_seconds = min(
                wait_domain_seconds * 1.5, _OVEN_RETRY_BACKOFF_CAP_DOMAIN_SECONDS
            )

    async def _scrap(self, ticket_id: str, item_id: str) -> None:
        reason = self._rng.choice(_SCRAP_REASONS)
        response = await self._client.post(
            f"/kitchen/tickets/{ticket_id}/items/{item_id}/scrap", {"reason": reason}
        )
        self._ledger.record_request(
            "POST", "/kitchen/tickets/{tid}/items/{iid}/scrap", response.status_code
        )
        if response.status_code != httpx.codes.OK:
            return  # order no longer in progress; nothing was scrapped
        self._ledger.record_scrap()
        self._ledger.record_remake()
        logger.info("chef scrapped item", item_id=item_id, ticket_id=ticket_id, reason=reason)
