"""Consumes the service stream, walks a distance-proportional delay,
delivers. Renders nothing — proof the API is sufficient for a frontend that
was never written. See docs/simulator.md "Actors" and
docs/adr/0008-simulator-as-external-client.md.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx

from simulator.log import get_logger

if TYPE_CHECKING:
    from random import Random

    from simulator.clock import ScaledClock
    from simulator.http_client import FornoClient
    from simulator.shadow_ledger import ShadowLedger

logger = get_logger(__name__)

_RECONNECT_DELAY_SECONDS = 2.0
_MIN_WALK_SECONDS = 2.0
_MAX_WALK_SECONDS = 8.0


class WaiterActor:
    def __init__(
        self,
        client: FornoClient,
        ledger: ShadowLedger,
        clock: ScaledClock,
        rng: Random,
        claimed_orders: set[str] | None = None,
    ) -> None:
        self._client = client
        self._ledger = ledger
        self._clock = clock
        self._rng = rng
        self._background_tasks: set[asyncio.Task[None]] = set()
        # Shared between every waiter on the floor: whoever calls "I've got
        # table 7" first carries it. Two waiters delivering the same order is
        # what the API's idempotent `delivered` absorbs — but the simulator
        # counts observed deliveries, so the waiters must not race each other.
        self._claimed_for_delivery: set[str] = (
            claimed_orders if claimed_orders is not None else set()
        )

    async def run(self) -> None:
        """Consumes `/streams/service` and carries out whole orders on
        `order.ready`. Individual `item.ready` frames are informational: the
        API refuses delivery until the order itself is READY (fire-together,
        docs/domain-model.md invariant #9), so the first pizza waits for the
        rest. Reconnects with the last seen event id on a dropped connection."""
        last_event_id: str | None = None
        while True:
            try:
                # Refresh-and-recover (docs/architecture.md "Streaming to
                # clients"): the stream has no replay, so on every (re)connect
                # the read model is swept for orders that went READY while the
                # waiter was not listening.
                await self._sweep_ready_orders()
                async for event in self._client.stream(
                    "/streams/service", last_event_id=last_event_id
                ):
                    last_event_id = event["id"]
                    self._ledger.record_sse_event("service", event["event"], event["data"])
                    if event["event"] == "order.ready":
                        self._claim(event["data"]["order_id"])
            except (httpx.HTTPError, httpx.StreamError):
                await asyncio.sleep(_RECONNECT_DELAY_SECONDS)
                continue
            # On normal stream end, also sleep before reconnecting
            await asyncio.sleep(_RECONNECT_DELAY_SECONDS)

    def _claim(self, order_id: str) -> None:
        if order_id in self._claimed_for_delivery:
            return
        self._claimed_for_delivery.add(order_id)
        task = asyncio.create_task(self._deliver_order(order_id))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _sweep_ready_orders(self) -> None:
        response = await self._client.get("/orders", params={"status": "READY", "limit": 50})
        self._ledger.record_request("GET", "/orders", response.status_code)
        if response.status_code != httpx.codes.OK:
            return
        for order in response.json():
            self._claim(order["id"])

    async def _deliver_order(self, order_id: str) -> None:
        """One walk to the table, every READY item on the tray. The order is
        re-read from the API rather than reconstructed from events: the read
        model is the source of truth, the stream only says "look now"."""
        walk_seconds = self._rng.uniform(_MIN_WALK_SECONDS, _MAX_WALK_SECONDS)
        await self._clock.sleep_domain_seconds(walk_seconds)
        try:
            response = await self._client.get(f"/orders/{order_id}")
            self._ledger.record_request("GET", "/orders/{id}", response.status_code)
            if response.status_code != httpx.codes.OK:
                return
            order: dict[str, Any] = response.json()
            for item in order["items"]:
                if item["status"] == "READY":
                    await self._deliver_item(order_id, item["id"])
        except httpx.HTTPError:
            return  # the run is shutting down and the client is gone

    async def _deliver_item(self, order_id: str, item_id: str) -> None:
        response = await self._client.post(f"/orders/{order_id}/items/{item_id}/delivered", {})
        self._ledger.record_request(
            "POST", "/orders/{id}/items/{iid}/delivered", response.status_code
        )
        if response.status_code == httpx.codes.OK:
            self._ledger.record_delivery(item_id)
            logger.info("waiter delivered item", item_id=item_id, order_id=order_id)
