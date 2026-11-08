"""Entry point: `python -m simulator`. Env-var driven so the same image works
under `docker compose --profile sim` — see docker-compose.yml `simulator`
service and docs/simulator.md "Running it".

    FORNO_SIM_API_URL
    FORNO_SIM_SCENARIO           (scenario name, read from $FORNO_SIM_SCENARIO_DIR/<name>.yaml)
    FORNO_SIM_SCENARIO_DIR       (default /scenarios, the compose mount; forno_mcp points it at
                                  the repo's scenarios/ or at a re-timed copy)
    FORNO_SIM_SEED
    FORNO_SIM_DURATION_MINUTES   (overrides the scenario file, optional)
    FORNO_SIM_BOOTSTRAP_ONLY     (run ManagerActor.bootstrap(), then exit — no actors, no wait;
                                  used by `make seed` to populate a pizzeria, no traffic started)

Spawns one asyncio task per long-lived actor (manager, arrival generator, one
task per chef/waiter) plus one short-lived task per customer party, runs for
`duration_minutes` (scaled by `time_scale`) — or until SIGTERM/SIGINT if
`duration_minutes` is 0 — then, for a finite run with a scenario `expect`
block, prints the invariant report and exits non-zero on any failure. See
docs/simulator.md "The shadow ledger".
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx

from simulator.actors.arrival_generator import ArrivalGenerator, rng_for
from simulator.actors.cashier import CashierActor
from simulator.actors.chef import ChefActor
from simulator.actors.customer import CustomerActor
from simulator.actors.manager import ManagerActor
from simulator.actors.waiter import WaiterActor
from simulator.catalog import ItemIndex, MenuCatalog
from simulator.clock import ScaledClock
from simulator.http_client import FornoClient
from simulator.log import configure_logging, get_logger
from simulator.models import Channel
from simulator.report import print_report
from simulator.scenario import Scenario, TableConfig
from simulator.shadow_ledger import ShadowLedger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger(__name__)

# Deliberate stand-in for real auth, matching forno.adapters.http.deps.DEV_API_KEY
# — the simulator cannot import forno, so this is duplicated by necessity.
# See docs/api.md "Conventions" ("On authentication").
_DEV_API_KEY = "dev"


def time_scale_mismatch(ready_payload: dict[str, Any], scenario_time_scale: int) -> str | None:
    """The API computes promises from domain durations at its own
    `FORNO_TIME_SCALE` (ADR-0009). Running a 120x scenario against a real-time
    API makes every promise 120 times too generous, so the two must agree.
    Returns a human-readable reason, or None when they do."""
    server_scale = ready_payload.get("time_scale")
    if server_scale is None:
        return "API did not report a time_scale on /health/ready (older server?)"
    if server_scale != scenario_time_scale:
        return (
            f"API runs at time_scale={server_scale} but the scenario declares "
            f"{scenario_time_scale}; start the stack with TIME_SCALE={scenario_time_scale} "
            "(make demo/make live do this from the scenario file)"
        )
    return None


async def _at(
    clock: ScaledClock,
    at_minute: float,
    action: Callable[[dict[str, Any]], Awaitable[None]],
    payload: dict[str, Any],
) -> None:
    """Runs `action(payload)` `at_minute` domain minutes into the service."""
    await clock.sleep_domain_seconds(at_minute * 60)
    await action(payload)


def _pick_free_table(tables: list[TableConfig], party_size: int, occupied: set[int]) -> int | None:
    candidates = sorted(
        (table for table in tables if table.seats >= party_size and table.number not in occupied),
        key=lambda table: table.seats,
    )
    return candidates[0].number if candidates else None


async def run(  # noqa: C901, PLR0915 -- single process-wiring function; splitting it would scatter the closures over `occupied_tables`/`cashier` that make it correct
    scenario_path: str,
    api_url: str,
    seed: int,
    duration_minutes: int | None,
    *,
    bootstrap_only: bool = False,
) -> int:
    scenario = Scenario.from_yaml(scenario_path)
    sim = scenario.simulation
    effective_duration = duration_minutes if duration_minutes is not None else sim.duration_minutes
    logger.info(
        "simulator starting",
        scenario=scenario.name,
        seed=seed,
        duration_minutes=effective_duration or "open-ended",
        bootstrap_only=bootstrap_only,
    )

    clock = ScaledClock(sim.time_scale)
    ledger = ShadowLedger()
    catalog = MenuCatalog()
    item_index = ItemIndex()

    manager_client = FornoClient(api_url, _DEV_API_KEY, "manager")
    cashier_client = FornoClient(api_url, _DEV_API_KEY, "cashier")
    waiter_client = FornoClient(api_url, _DEV_API_KEY, "waiter")
    chef_client = FornoClient(api_url, _DEV_API_KEY, "chef")
    all_clients = [manager_client, cashier_client, waiter_client, chef_client]

    tasks: list[asyncio.Task[None]] = []
    try:
        manager = ManagerActor(manager_client, ledger, scenario.pizzeria, catalog)
        await manager.bootstrap()
        logger.info(
            "manager bootstrapped pizzeria",
            menu_items=len(scenario.pizzeria.menu),
            ovens=len(scenario.pizzeria.ovens),
            tables=len(scenario.pizzeria.tables),
        )

        if bootstrap_only:
            return 0

        ready = await manager_client.get("/health/ready")
        mismatch = time_scale_mismatch(
            ready.json() if ready.status_code == httpx.codes.OK else {}, sim.time_scale
        )
        if mismatch is not None:
            logger.error("simulator refusing to start", reason=mismatch)
            return 2

        cashier = CashierActor(
            cashier_client,
            manager,
            ledger,
            item_index,
            catalog,
            sim.behaviour.double_submit_rate,
            rng_for("cashier", seed),
        )

        tasks.append(asyncio.create_task(manager.watch_floor_stream()))

        for i in range(scenario.pizzeria.staff.chefs):
            chef = ChefActor(
                chef_client,
                ledger,
                item_index,
                sim.behaviour.remake_rate,
                clock,
                rng_for(f"chef-{i}", seed),
            )
            tasks.append(asyncio.create_task(chef.run()))

        claimed_orders: set[str] = set()  # one floor: a READY order is carried by one waiter
        for i in range(scenario.pizzeria.staff.waiters):
            waiter = WaiterActor(
                waiter_client, ledger, clock, rng_for(f"waiter-{i}", seed), claimed_orders
            )
            tasks.append(asyncio.create_task(waiter.run()))

        occupied_tables: set[int] = set()

        def spawn_customer(party_size: int, channel: Channel) -> None:
            table_number: int | None = None
            if channel == Channel.DINE_IN:
                table_number = _pick_free_table(
                    scenario.pizzeria.tables, party_size, occupied_tables
                )
                if table_number is None:
                    channel = (
                        Channel.TAKEAWAY
                    )  # no table free — becomes a takeaway order, same as real life
                else:
                    occupied_tables.add(table_number)

            customer = CustomerActor(
                cashier,
                waiter_client,
                cashier_client,
                clock,
                catalog,
                ledger,
                sim.behaviour,
                rng_for(f"customer-{uuid4()}", seed),
            )

            async def _visit() -> None:
                try:
                    await customer.visit(party_size, channel, table_number)
                finally:
                    if table_number is not None:
                        occupied_tables.discard(table_number)

            tasks.append(asyncio.create_task(_visit()))

        arrivals = ArrivalGenerator(sim.arrivals, sim.parties, seed)
        tasks.append(asyncio.create_task(arrivals.run(spawn_customer, clock)))

        # Scripted events (`faults:` / `menu_changes:`) fire at their domain
        # offset through the same management API an owner would use.
        tasks.extend(
            asyncio.create_task(_at(clock, fault["at_minute"], manager.apply_fault, fault))
            for fault in sim.faults
        )
        tasks.extend(
            asyncio.create_task(_at(clock, change["at_minute"], manager.apply_menu_change, change))
            for change in sim.menu_changes
        )

        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop_event.set)

        if effective_duration > 0:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    stop_event.wait(), timeout=effective_duration * 60 / sim.time_scale
                )
        else:
            await stop_event.wait()
        logger.info("simulator stopping", orders_placed=ledger.orders_placed)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for client in all_clients:
            await client.aclose()

    if effective_duration > 0 and sim.expect is not None:
        results = ledger.check_invariants(sim.expect)
        return print_report(
            scenario.name, seed, effective_duration, sim.time_scale, ledger, results
        )
    return 0


def main() -> int:
    configure_logging(os.environ.get("FORNO_SIM_LOG_LEVEL", "INFO"))
    scenario_name = os.environ.get("FORNO_SIM_SCENARIO", "busy-friday")
    scenario_dir = os.environ.get("FORNO_SIM_SCENARIO_DIR", "/scenarios")
    scenario_path = f"{scenario_dir}/{scenario_name}.yaml"
    api_url = os.environ["FORNO_SIM_API_URL"]
    seed = int(os.environ.get("FORNO_SIM_SEED", "42"))
    duration_env = os.environ.get("FORNO_SIM_DURATION_MINUTES", "")
    duration_minutes = int(duration_env) if duration_env else None
    bootstrap_only = os.environ.get("FORNO_SIM_BOOTSTRAP_ONLY", "").lower() in ("1", "true")
    return asyncio.run(
        run(scenario_path, api_url, seed, duration_minutes, bootstrap_only=bootstrap_only)
    )


if __name__ == "__main__":
    sys.exit(main())
