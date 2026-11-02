"""Takes orders, retries on 429 (handled inside `FornoClient`), occasionally
double-submits to exercise the idempotency path, and — the single most
valuable behaviour in the simulator — routes a 409 cancel to the manager for
a void, exactly as a human would. See docs/simulator.md "Actors" and "Actors
that behave like real clients, badly".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx

from simulator.log import get_logger

if TYPE_CHECKING:
    from random import Random

    from simulator.actors.manager import ManagerActor
    from simulator.catalog import ItemIndex, MenuCatalog
    from simulator.http_client import FornoClient
    from simulator.shadow_ledger import ShadowLedger

logger = get_logger(__name__)


class CashierActor:
    def __init__(  # noqa: PLR0913 -- explicit dependency injection of collaborators
        self,
        client: FornoClient,
        manager: ManagerActor,
        ledger: ShadowLedger,
        item_index: ItemIndex,
        catalog: MenuCatalog,
        double_submit_rate: float,
        rng: Random,
    ) -> None:
        self._client = client
        self._manager = manager
        self._ledger = ledger
        self._item_index = item_index
        self._catalog = catalog
        self._double_submit_rate = double_submit_rate
        self._rng = rng

    async def place_order(
        self, channel: str, table_number: int | None, lines: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Occasionally double-submits with the same `Idempotency-Key`,
        exercising the replay path per `double_submit_rate`."""
        idempotency_key = str(uuid4())
        body = {"channel": channel, "table_number": table_number, "lines": lines}

        response = await self._client.post("/orders", body, idempotency_key=idempotency_key)
        self._ledger.record_request("POST", "/orders", response.status_code)
        if response.status_code != httpx.codes.CREATED:
            logger.info("cashier order rejected", channel=channel, status_code=response.status_code)
            return None
        order: dict[str, Any] = response.json()
        logger.info(
            "cashier placed order",
            order_id=order["id"],
            channel=channel,
            items=len(order["items"]),
        )

        if self._rng.random() < self._double_submit_rate:
            duplicate = await self._client.post("/orders", body, idempotency_key=idempotency_key)
            self._ledger.record_request("POST", "/orders", duplicate.status_code)

        self._item_index.register_order(
            order_item_ids=[item["id"] for item in order["items"]],
            menu_item_ids=[item["menu_item_id"] for item in order["items"]],
            catalog=self._catalog,
        )
        self._ledger.record_order_placed(item_count=len(order["items"]))
        return order

    async def cancel_or_escalate(self, order_id: str) -> None:
        """On 409, hands off to the manager for a void instead of retrying —
        this is the behaviour an in-process harness would never discover."""
        response = await self._client.post(f"/orders/{order_id}/cancel", {})
        self._ledger.record_request("POST", "/orders/{id}/cancel", response.status_code)
        if response.status_code == httpx.codes.CONFLICT:
            await self._manager.void_order(order_id, "walkout")
            self._ledger.record_order_voided()
        else:
            logger.info("cashier cancelled order", order_id=order_id, reason="walkout")
            self._ledger.record_order_cancelled()

    async def close_order(self, order_id: str) -> None:
        response = await self._client.post(f"/orders/{order_id}/close", {})
        self._ledger.record_request("POST", "/orders/{id}/close", response.status_code)
        logger.info("cashier closed order", order_id=order_id)
