"""Reads the scenario, configures the pizzeria through the management API,
watches the floor stream, voids orders a cashier could not cancel. See
docs/configuration.md "Bootstrap flow" and docs/simulator.md "Actors".

Bootstrap order matters: menu, stations, tables, shifts, then wait for
`/health/ready` and menu size == n before the arrival generator starts.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx

from simulator.log import get_logger

if TYPE_CHECKING:
    from simulator.catalog import MenuCatalog
    from simulator.http_client import FornoClient
    from simulator.scenario import PizzeriaConfig
    from simulator.shadow_ledger import ShadowLedger

logger = get_logger(__name__)

_READY_POLL_SECONDS = 1.0
_READY_POLL_ATTEMPTS = 60
_RECONNECT_DELAY_SECONDS = 2.0


class ManagerActor:
    def __init__(
        self,
        client: FornoClient,
        ledger: ShadowLedger,
        config: PizzeriaConfig,
        catalog: MenuCatalog,
    ) -> None:
        self._client = client
        self._ledger = ledger
        self._config = config
        self._catalog = catalog

    async def bootstrap(self) -> None:
        for item in self._config.menu:
            resp = await self._client.post(
                "/menu/items",
                {
                    "sku": item.sku,
                    "name": item.name,
                    "price": str(item.price),
                    "vat": str(item.vat),
                    "prep_seconds": item.prep_seconds,
                    "bake_seconds": item.bake_seconds,
                },
            )
            self._ledger.record_request("POST", "/menu/items", resp.status_code)
            if resp.status_code == httpx.codes.CONFLICT:
                # Bootstrap re-runs on every simulator start (docs/configuration.md
                # "Bootstrap flow") — a SKU collision means this item already
                # exists from a prior run, not a real error. Look its id up.
                existing = await self._client.get("/menu")
                existing.raise_for_status()
                match = next(m for m in existing.json() if m["sku"] == item.sku)
                self._catalog.register(match["id"], item)
                continue
            resp.raise_for_status()
            self._catalog.register(resp.json()["id"], item)

        # Ovens/stations/tables are keyed by a caller-chosen id (name/number),
        # so a re-run collides on the primary key the same way menu items
        # collide on sku — 409 means "already configured", not a real error.
        for oven in self._config.ovens:
            resp = await self._client.post(
                "/stations",
                {
                    "kind": "oven",
                    "name": oven.name,
                    "slots": oven.slots,
                    "bake_seconds": oven.bake_seconds,
                },
            )
            self._ledger.record_request("POST", "/stations", resp.status_code)
            if resp.status_code != httpx.codes.CONFLICT:
                resp.raise_for_status()

        for kind, station in self._config.stations.items():
            resp = await self._client.post(
                "/stations",
                {
                    "kind": kind,
                    "name": kind,
                    "slots": station.count,
                    "bake_seconds": station.seconds_per_item,
                },
            )
            self._ledger.record_request("POST", "/stations", resp.status_code)
            if resp.status_code != httpx.codes.CONFLICT:
                resp.raise_for_status()

        for table in self._config.tables:
            resp = await self._client.post(
                "/tables", {"number": table.number, "seats": table.seats}
            )
            self._ledger.record_request("POST", "/tables", resp.status_code)
            if resp.status_code != httpx.codes.CONFLICT:
                resp.raise_for_status()

        resp = await self._client.post(
            "/shifts",
            {
                "cashiers": self._config.staff.cashiers,
                "chefs": self._config.staff.chefs,
                "waiters": self._config.staff.waiters,
            },
        )
        self._ledger.record_request("POST", "/shifts", resp.status_code)
        resp.raise_for_status()

        await self._await_ready(expected_menu_size=len(self._config.menu))

    async def _await_ready(self, expected_menu_size: int) -> None:
        for _attempt in range(_READY_POLL_ATTEMPTS):
            ready = await self._client.get("/health/ready")
            if ready.status_code == httpx.codes.OK:
                menu = await self._client.get("/menu")
                if menu.status_code == httpx.codes.OK and len(menu.json()) >= expected_menu_size:
                    return
            await asyncio.sleep(_READY_POLL_SECONDS)
        msg = "pizzeria did not become ready in time"
        raise RuntimeError(msg)

    async def watch_floor_stream(self) -> None:
        """Consumes `/streams/floor`. Reconnects with the last seen event id
        on a dropped connection, mirroring `WaiterActor.run`."""
        last_event_id: str | None = None
        while True:
            try:
                async for event in self._client.stream(
                    "/streams/floor", last_event_id=last_event_id
                ):
                    last_event_id = event["id"]
                    self._ledger.record_sse_event("floor", event["event"], event["data"])
            except (httpx.HTTPError, httpx.StreamError):
                await asyncio.sleep(_RECONNECT_DELAY_SECONDS)
                continue
            # On normal stream end, also sleep before reconnecting
            await asyncio.sleep(_RECONNECT_DELAY_SECONDS)

    async def apply_fault(self, fault: dict[str, Any]) -> None:
        """`oven-down` scenario support: takes an oven out of service through
        the management API at a scheduled offset, exactly as an owner
        reporting a broken oven would. See scenarios/oven-down.yaml."""
        if fault.get("action") != "disable_oven":
            logger.warning("manager ignoring unknown fault", fault=fault)
            return
        resp = await self._client.patch(f"/stations/{fault['oven']}", {"active": False})
        self._ledger.record_request("PATCH", "/stations/{id}", resp.status_code)
        logger.info("manager took oven out of service", oven=fault["oven"], status=resp.status_code)

    async def apply_menu_change(self, change: dict[str, Any]) -> None:
        """`price-change` scenario support: `PATCH /menu/items/{id}` at a
        scheduled offset. See scenarios/price-change.yaml."""
        menu_item_id = next(
            item_id
            for item_id, config in self._catalog.by_id.items()
            if config.sku == change["sku"]
        )
        resp = await self._client.patch(
            f"/menu/items/{menu_item_id}", {"price": str(change["price"])}
        )
        self._ledger.record_request("PATCH", "/menu/items/{id}", resp.status_code)
        logger.info("manager changed price", sku=change["sku"], price=change["price"])

    async def void_order(self, order_id: str, reason: str) -> None:
        """Manager-only reversal for an order a cashier could not cancel
        (409 — an item is already `IN_OVEN`). See docs/simulator.md "Actors
        that behave like real clients, badly"."""
        resp = await self._client.post(f"/orders/{order_id}/void", {"reason": reason})
        self._ledger.record_request("POST", "/orders/{id}/void", resp.status_code)
        logger.info("manager voided order", order_id=order_id, reason=reason)
