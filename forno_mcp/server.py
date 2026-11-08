"""The MCP surface: what an agent sees when it connects to Forno.

Three kinds of things, following the protocol's own split:

* Resources: the documentation an operator reads (domain model, runbook,
  ADRs, scenarios). The agent loads context instead of guessing what
  IN_KITCHEN or a 409 means.
* Tools: observe (snapshot, triage, live streams, one order explained),
  then act (run a simulation against the live stack, take an oven out of
  service). Read-only tools say so in their annotations; the ones that change
  the pizzeria are marked destructive so a client can ask a human first.
* Prompts: the three investigations an operator actually runs.

Every tool call is logged as one JSON line on stderr (stdout carries the
protocol under stdio) — the same structured-logging convention as the API.
"""

from __future__ import annotations

import functools
import json
import logging
import sys
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from forno_mcp import knowledge, live, triage
from forno_mcp.clients import FornoApi, Prometheus, UnreachableError
from forno_mcp.config import Settings
from forno_mcp.simulations import SimulationRunner
from forno_mcp.snapshot import PROM_SIGNALS, take_snapshot

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

INSTRUCTIONS = """\
You are connected to Forno, an event-driven pizzeria backend (FastAPI, Postgres, Redpanda,
Prometheus). Postgres is the system of record; Kafka only propagates. Promises (promised_at) are
fixed when an order is accepted and are the basis of a 95% SLO.

Start with get_service_snapshot. If something looks wrong, call diagnose: it walks the runbook's
triage tree and separates "the software is broken" from "there are more customers than chefs".
Use watch_live to see what is happening over the next seconds, and explain_order for one order.
Read forno://docs/domain-model and forno://docs/runbook when you need the rules.

start_simulation runs a scenario (busy-friday, understaffed, oven-down, ...) against the live
system in the background; poll get_simulation and read its invariant report. set_oven_active is
real chaos on the live system: confirm with the user first.

Durations in metrics are scaled domain seconds (ADR-0009); timestamps are wall-clock UTC.
"""

ALL_STREAMS = list(live.STREAM_ROLES)


def _log(event: str, **fields: Any) -> None:  # noqa: ANN401 -- arbitrary JSON log fields
    record = {"ts": datetime.now(UTC).isoformat(timespec="milliseconds"), "event": event, **fields}
    print(json.dumps(record, default=str), file=sys.stderr, flush=True)  # noqa: T201 -- stderr is the log sink; stdout is the protocol


def logged[**P, R](fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Log every tool call, and turn expected failures into errors the agent
    can act on. Anything unexpected stays a crash: the SDK hides its details
    from the agent and they remain in this log."""

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        start = time.perf_counter()
        try:
            result = await fn(*args, **kwargs)
        except (ValueError, RuntimeError, UnreachableError, httpx.HTTPStatusError) as exc:
            _log("tool_call", tool=fn.__name__, args=kwargs, ok=False, error=str(exc))
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            _log("tool_call", tool=fn.__name__, args=kwargs, ok=False, crash=repr(exc))
            raise
        ms = round((time.perf_counter() - start) * 1000, 1)
        _log("tool_call", tool=fn.__name__, args=kwargs, ok=True, duration_ms=ms)
        return result

    return wrapper


def _explain(order: dict[str, Any], now: datetime) -> dict[str, Any]:
    status = order["status"]
    promised = order.get("promised_at")
    timing: dict[str, Any] = {"placed_at": order.get("placed_at"), "promised_at": promised}
    if promised:
        promised_dt = datetime.fromisoformat(promised)
        ready = order.get("ready_at")
        if ready:
            ready_dt = datetime.fromisoformat(ready)
            delta = (ready_dt - promised_dt).total_seconds()
            timing["outcome"] = (
                f"ready {abs(delta):.0f}s {'late' if delta > 0 else 'early'} (wall clock)"
            )
        elif status in knowledge.ORDER_OPEN:
            delta = (now - promised_dt).total_seconds()
            timing["outcome"] = (
                f"not ready, {delta:.0f}s past promise"
                if delta > 0
                else f"not ready, {-delta:.0f}s left before promise"
            )
    items = order.get("items", [])
    remakes = [i for i in items if i.get("remake_of")]
    in_oven = any(i["status"] in {"IN_OVEN", "READY", "DELIVERED"} for i in items)
    # The domain refuses a cancel once any item has *ever* been in an oven. The
    # API does not say whether a SCRAPPED item was scrapped before or after
    # baking, so with one present the honest answer is "unknown" (None).
    scrapped = any(i["status"] == "SCRAPPED" for i in items)
    can_cancel: bool | None = status in {"ACCEPTED", "IN_KITCHEN"} and not in_oven
    if can_cancel and scrapped:
        can_cancel = None
    return {
        "order_id": order["id"],
        "status": status,
        "meaning": knowledge.ORDER_STATE_MEANING.get(status, ""),
        "legal_next_states": list(knowledge.ORDER_TRANSITIONS.get(status, ())),
        "can_still_be_cancelled": can_cancel,
        "channel": order.get("channel"),
        "table": order.get("table_number"),
        "timing": timing,
        "items": [
            {
                "name": i.get("name"),
                "status": i["status"],
                "fire_group": i.get("fire_group"),
                "next": list(knowledge.ITEM_TRANSITIONS.get(i["status"], ())),
                "remake_of": i.get("remake_of"),
            }
            for i in items
        ],
        "remakes": len(remakes),
        "total_gross": f"{order.get('total_gross')} {order.get('currency', '')}".strip(),
        "notes": (
            ["Remakes inherit the original promised_at, so they count against the SLO."]
            if remakes
            else []
        ),
    }


def build_server(  # noqa: C901, PLR0915 -- one registration function, flat on purpose
    settings: Settings | None = None,
    *,
    api_transport: httpx.AsyncBaseTransport | None = None,
    prom_transport: httpx.AsyncBaseTransport | None = None,
) -> MCPServer:
    settings = settings or Settings.from_env()
    api = FornoApi(settings, transport=api_transport)
    prom = Prometheus(settings, transport=prom_transport)
    sims = SimulationRunner(settings, api)
    root = settings.repo_root

    mcp = MCPServer(name="forno", instructions=INSTRUCTIONS, version="0.1.0")
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)
    changes_pizzeria = ToolAnnotations(destructive_hint=True, open_world_hint=False)

    # --- Observe ------------------------------------------------------------

    @mcp.tool(annotations=read_only)
    @logged
    async def get_service_snapshot() -> dict[str, Any]:
        """What is going on right now: API health and time scale, open orders by status and
        which are already late, kitchen tickets per station, oven occupancy, tables, today's
        stats, SLO/pipeline signals from Prometheus and any firing alerts. Starts with a
        plain-language headline."""
        return (await take_snapshot(api, prom)).public()

    @mcp.tool(annotations=read_only)
    @logged
    async def diagnose() -> dict[str, Any]:
        """Walk the runbook's triage tree on live data and say whether a problem is the software
        (outbox, projectors, DLQ, API) or the kitchen (capacity, prep bottleneck, estimator,
        remakes). Returns the decision path with the values that decided each branch, the
        recommended actions and the matching runbook section."""
        snap = await take_snapshot(api, prom)
        return triage.diagnose(snap).public(root)

    @mcp.tool(annotations=read_only)
    @logged
    async def watch_live(seconds: float = 20, streams: list[str] | None = None) -> dict[str, Any]:
        """Subscribe to the SSE streams (kitchen, service, floor) for a few seconds (max 120)
        and summarise what happened: orders placed/ready/closed, kitchen item transitions,
        which ovens were loaded, plus the latest events."""
        return await live.watch(api, seconds, streams or ALL_STREAMS)

    @mcp.tool(annotations=read_only)
    @logged
    async def explain_order(order_id: str) -> dict[str, Any]:
        """One order in context: what its state means, which transitions are still legal,
        whether it can still be cancelled, every item's stage, and how it stands against its
        promise."""
        resp = await api.request("GET", f"/orders/{order_id}")
        if resp.status_code == httpx.codes.NOT_FOUND:
            msg = f"No order {order_id}. Use list_orders to find one."
            raise ValueError(msg)
        resp.raise_for_status()
        return _explain(resp.json(), datetime.now(UTC))

    @mcp.tool(annotations=read_only)
    @logged
    async def list_orders(
        status: str | None = None, table: int | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Recent orders, compact: id, status, table, placed/promised/ready times, item stages.
        Filter by status (PLACED, ACCEPTED, IN_KITCHEN, READY, SERVED, CLOSED, ...) or table."""
        params: dict[str, Any] = {"limit": max(1, min(limit, 200))}
        if status:
            params["status"] = status.upper()
        if table is not None:
            params["table"] = table
        orders = await api.get_json("/orders", params=params)
        return [
            {
                "id": o["id"],
                "status": o["status"],
                "table": o.get("table_number"),
                "placed_at": o.get("placed_at"),
                "promised_at": o.get("promised_at"),
                "ready_at": o.get("ready_at"),
                "items": [f"{i.get('name')}:{i['status']}" for i in o.get("items", [])],
            }
            for o in orders
        ]

    @mcp.tool(annotations=read_only)
    @logged
    async def query_metric(promql: str) -> dict[str, Any]:
        """Run an instant PromQL query against Prometheus. The metric catalogue is in
        forno://docs/observability; the snapshot's signals show useful starting queries."""
        series = await prom.vector(promql)
        return {
            "query": promql,
            "series": [{"labels": labels, "value": value} for labels, value in series[:50]],
            "truncated": len(series) > 50,  # noqa: PLR2004
            "starting_points": PROM_SIGNALS,
        }

    @mcp.tool(annotations=read_only)
    @logged
    async def list_scenarios() -> list[dict[str, Any]]:
        """Scenario files the simulator can run: capacity, arrival rate, injected faults and
        the outcome each one expects (e.g. understaffed is meant to breach the SLO)."""
        return [s.public() for s in knowledge.list_scenarios(settings.scenarios_dir)]

    # --- Act ----------------------------------------------------------------

    @mcp.tool(annotations=changes_pizzeria)
    @logged
    async def start_simulation(
        scenario: str,
        seed: int = 42,
        duration_minutes: int | None = None,
        match_api_time_scale: bool = True,  # noqa: FBT001, FBT002 -- MCP tool arguments are keywords on the wire
    ) -> dict[str, Any]:
        """Start the simulator against the live stack in the background and return a job_id
        at once. It bootstraps the scenario's pizzeria through the management API, drives
        customers, cashier, chefs and waiters through the public API, and ends with the
        invariant report. Poll get_simulation. Only one simulation runs at a time."""
        job = await sims.start(
            scenario,
            seed=seed,
            duration_minutes=duration_minutes,
            match_api_time_scale=match_api_time_scale,
        )
        return job.public()

    @mcp.tool(annotations=read_only)
    @logged
    async def get_simulation(job_id: str, full_log: bool = False) -> dict[str, Any]:  # noqa: FBT001, FBT002
        """State and progress of a simulation, the latest simulator log lines, and — once it
        has finished — the invariant report."""
        return sims.get(job_id).public(full_log=full_log)

    @mcp.tool(annotations=read_only)
    @logged
    async def list_simulations() -> list[dict[str, Any]]:
        """Simulations started in this session, newest first."""
        return [{k: v for k, v in j.public().items() if k != "recent_log"} for j in sims.list()]

    @mcp.tool(annotations=ToolAnnotations(destructive_hint=True, idempotent_hint=True))
    @logged
    async def stop_simulation(job_id: str) -> dict[str, Any]:
        """Stop a running simulation early. The simulator still prints its report for what
        it saw so far."""
        return (await sims.stop(job_id)).public()

    @mcp.tool(annotations=changes_pizzeria)
    @logged
    async def set_oven_active(oven_id: str, active: bool, reason: str) -> dict[str, Any]:  # noqa: FBT001
        """Take an oven out of service (active=false) or bring it back. Pizzas already inside
        finish; nothing new goes in. This is real chaos on the live pizzeria — the same call
        the oven-down scenario makes — so confirm with the user first. `reason` is logged."""
        resp = await api.request(
            "PATCH", f"/stations/{oven_id}", role="manager", json={"active": active}
        )
        if resp.status_code == httpx.codes.NOT_FOUND:
            msg = f"No station '{oven_id}'. get_service_snapshot lists the ovens."
            raise ValueError(msg)
        resp.raise_for_status()
        _log("oven_toggled", oven=oven_id, active=active, reason=reason)
        return {"oven": oven_id, "active": active, "reason": reason, "station": resp.json()}

    # --- Resources ----------------------------------------------------------

    def _doc_reader(name: str) -> Callable[[], str]:
        def read() -> str:
            return knowledge.read_doc(root, name)

        return read

    for doc_name, (path, description) in knowledge.DOC_RESOURCES.items():
        mcp.resource(
            f"forno://docs/{doc_name}",
            name=doc_name,
            description=f"{description} ({path})",
            mime_type="text/markdown",
        )(_doc_reader(doc_name))

    @mcp.resource("forno://adr", name="adr-index", mime_type="application/json")
    def adr_index() -> list[dict[str, str]]:
        """Architecture decision records: id and title. Read one with forno://adr/{id}."""
        return knowledge.list_adrs(root)

    @mcp.resource("forno://adr/{adr_id}", name="adr", mime_type="text/markdown")
    def adr(adr_id: str) -> str:
        """One architecture decision record, e.g. forno://adr/0002 (transactional outbox)."""
        return knowledge.read_adr(root, adr_id)

    @mcp.resource("forno://scenarios/{name}", name="scenario", mime_type="text/yaml")
    def scenario_file(name: str) -> str:
        """A simulator scenario file as YAML, e.g. forno://scenarios/oven-down."""
        path = settings.scenarios_dir / f"{name}.yaml"
        if not path.exists():
            msg = f"No scenario '{name}'."
            raise ValueError(msg)
        return path.read_text(encoding="utf-8")

    @mcp.resource("forno://state-machines", name="state-machines", mime_type="application/json")
    def state_machines() -> dict[str, Any]:
        """Order and item lifecycles: legal transitions and what each order state means."""
        return {
            "order_transitions": knowledge.ORDER_TRANSITIONS,
            "order_terminal_states": knowledge.ORDER_TERMINAL,
            "order_state_meaning": knowledge.ORDER_STATE_MEANING,
            "item_transitions": knowledge.ITEM_TRANSITIONS,
            "source": "docs/domain-model.md (the transition table is the specification)",
        }

    # --- Prompts ------------------------------------------------------------

    @mcp.prompt(name="incident-triage", title="Triage an alert or a complaint")
    def incident_triage(symptom: str = "an alert fired") -> str:
        return (
            f"Something is wrong at Forno: {symptom}.\n"
            "1. Call get_service_snapshot and read its headline.\n"
            "2. Call diagnose. Report its verdict and the decision path with the numbers.\n"
            "3. If the verdict is 'software', name the failing component and quote the runbook "
            "steps. If it is capacity, say which resource is the bottleneck.\n"
            "4. Call watch_live for 20 seconds to confirm the picture is current.\n"
            "Do not change anything (no set_oven_active, no simulations) without asking me."
        )

    @mcp.prompt(name="why-is-the-kitchen-slow", title="Explain late orders")
    def why_slow() -> str:
        return (
            "Orders are coming out late. Find out why.\n"
            "Use get_service_snapshot, then explain_order on the two latest-late orders, then "
            "diagnose. Separate 'too many customers for the ovens/chefs' from 'the promise "
            "estimator is too optimistic' (forno://docs/domain-model, section 'Promised time'). "
            "Finish with one recommendation and the evidence for it."
        )

    @mcp.prompt(name="run-experiment", title="Run a scenario and interpret it")
    def run_experiment(scenario: str = "oven-down") -> str:
        return (
            f"Run the '{scenario}' scenario against the live stack and interpret the result.\n"
            "1. list_scenarios: say what this scenario is designed to show and what it expects.\n"
            "2. get_service_snapshot as a baseline.\n"
            f"3. start_simulation(scenario='{scenario}'), then poll get_simulation; while it "
            "runs, call watch_live once and diagnose once.\n"
            "4. When it finishes, report the invariant line, the breach rate and whether the "
            "outcome matched the scenario's expectation, and explain why using the evidence."
        )

    return mcp


def run(
    transport: Literal["stdio", "streamable-http"] = "stdio",
    **kwargs: Any,  # noqa: ANN401 -- forwarded to MCPServer.run (host, port)
) -> None:
    server = build_server()
    _log("server_start", transport=transport)
    # The SDK logs at INFO, which makes httpx print a line per request to
    # stderr; our own `tool_call` record already covers each call.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    server.run(transport, **kwargs)
