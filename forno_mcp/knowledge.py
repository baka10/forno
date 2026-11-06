"""What the agent needs to *understand* Forno, not just call it.

The state machines are copied from the domain (docs/domain-model.md says the
transition table is the specification) because this package may not import
`forno`. A test pins them against the markdown so the copy cannot drift
silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from pathlib import Path

ORDER_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "PLACED": ("ACCEPTED", "REJECTED"),
    "ACCEPTED": ("IN_KITCHEN", "CANCELLED"),
    "IN_KITCHEN": ("READY", "CANCELLED", "VOIDED"),
    "READY": ("SERVED", "ABANDONED", "VOIDED"),
    "SERVED": ("CLOSED", "VOIDED"),
}
ORDER_TERMINAL = ("REJECTED", "CANCELLED", "ABANDONED", "CLOSED", "VOIDED")
ORDER_OPEN = ("PLACED", "ACCEPTED", "IN_KITCHEN", "READY", "SERVED")

ITEM_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "QUEUED": ("PREPPING",),
    "PREPPING": ("IN_OVEN", "SCRAPPED"),
    "IN_OVEN": ("READY", "SCRAPPED"),
    "READY": ("DELIVERED",),
}

# What each order state means operationally, and who acts next. Written for an
# agent reading a snapshot, so it says "who is waiting on whom".
ORDER_STATE_MEANING: dict[str, str] = {
    "PLACED": "Received, not yet accepted. Nothing promised yet.",
    "ACCEPTED": "Accepted, promised_at fixed. Waiting for a chef to start the first item.",
    "IN_KITCHEN": (
        "At least one item is being prepped or baked. Cancel is still legal only while no "
        "item has entered the oven; after that the only exit is a manager void."
    ),
    "READY": "Every non-scrapped item is READY. Waiting for a waiter to deliver.",
    "SERVED": "Delivered. Waiting for the cashier to close the receipt.",
    "CLOSED": "Paid and finished.",
    "CANCELLED": "Cancelled before anything was baked.",
    "VOIDED": "Written off by a manager after food was made (walkout, complaint).",
    "ABANDONED": "Ready but never collected.",
    "REJECTED": "Never accepted.",
}

# Alert name -> runbook heading (docs/runbook.md), from the `runbook`
# annotations in deploy/prometheus/alert_rules.yml.
ALERT_RUNBOOK_SECTION: dict[str, str] = {
    "PromiseBudgetBurnFast": "PromiseBudgetBurn (Fast / Slow)",
    "PromiseBudgetBurnSlow": "PromiseBudgetBurn (Fast / Slow)",
    "OutboxLagHigh": "OutboxLagHigh / OutboxBacklogGrowing",
    "OutboxBacklogGrowing": "OutboxLagHigh / OutboxBacklogGrowing",
    "ProjectionLagHigh": "ProjectionLagHigh",
    "DLQNotEmpty": "DLQNotEmpty / PoisonEventDetected",
    "PoisonEventDetected": "DLQNotEmpty / PoisonEventDetected",
    "TicketAgeExceedsPromise": "TicketAgeExceedsPromise",
    "ApiErrorRate": "ApiErrorRate",
    "OvenSaturated": "Triage: the first sixty seconds",
    "RemakeRateHigh": "PromiseBudgetBurn (Fast / Slow)",
}

# Resources the agent can read. Key = MCP resource name, value = repo path.
DOC_RESOURCES: dict[str, tuple[str, str]] = {
    "domain-model": ("docs/domain-model.md", "Aggregates, state machines, invariants, promise"),
    "runbook": ("docs/runbook.md", "What to do when each alert fires; the triage tree"),
    "observability": ("docs/observability.md", "Metric catalogue, SLO, alerts, dashboards"),
    "api": ("docs/api.md", "Endpoints, roles, error model, SSE streams"),
    "simulator": ("docs/simulator.md", "Actors, determinism, time scaling, invariants"),
    "architecture": ("docs/architecture.md", "Containers, flows, layering"),
    "configuration": ("docs/configuration.md", "Scenario files and their knobs"),
}


def read_doc(repo_root: Path, name: str) -> str:
    if name not in DOC_RESOURCES:
        msg = f"Unknown document '{name}'. Available: {', '.join(DOC_RESOURCES)}."
        raise ValueError(msg)
    return (repo_root / DOC_RESOURCES[name][0]).read_text(encoding="utf-8")


def runbook_section(repo_root: Path, heading: str) -> str | None:
    """The body of one `## heading` section of docs/runbook.md."""
    text = (repo_root / "docs/runbook.md").read_text(encoding="utf-8")
    pattern = re.compile(rf"^## {re.escape(heading)}\s*$(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
    match = pattern.search(text)
    return match.group(1).strip().rstrip("-").strip() if match else None


def list_adrs(repo_root: Path) -> list[dict[str, str]]:
    adrs = []
    for path in sorted((repo_root / "docs/adr").glob("[0-9][0-9][0-9][0-9]-*.md")):
        first = path.read_text(encoding="utf-8").splitlines()[0]
        adrs.append({"id": path.stem[:4], "file": path.name, "title": first.lstrip("# ").strip()})
    return adrs


def read_adr(repo_root: Path, adr_id: str) -> str:
    matches = list((repo_root / "docs/adr").glob(f"{adr_id.zfill(4)}-*.md"))
    if not matches:
        msg = f"No ADR {adr_id}. Use the forno://adr index to see what exists."
        raise ValueError(msg)
    return matches[0].read_text(encoding="utf-8")


@dataclass(frozen=True, slots=True)
class ScenarioInfo:
    name: str
    description: str
    time_scale: int
    duration_minutes: int
    ovens: int
    oven_slots: int
    chefs: int
    arrivals_per_hour: float
    faults: list[dict[str, Any]]
    expect: dict[str, Any]

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "time_scale": self.time_scale,
            "duration_minutes": self.duration_minutes or "open-ended",
            "capacity": {
                "ovens": self.ovens,
                "oven_slots": self.oven_slots,
                "chefs": self.chefs,
            },
            "arrivals_per_hour": self.arrivals_per_hour,
            "faults": self.faults,
            "expect": self.expect,
        }


def load_scenario(path: Path) -> ScenarioInfo:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    pizzeria, sim = raw.get("pizzeria", {}), raw.get("simulation", {})
    ovens = pizzeria.get("ovens", [])
    return ScenarioInfo(
        name=raw.get("name", path.stem),
        description=" ".join(str(raw.get("description", "")).split()),
        time_scale=int(sim.get("time_scale", 1)),
        duration_minutes=int(sim.get("duration_minutes", 0)),
        ovens=len(ovens),
        oven_slots=sum(int(o.get("slots", 0)) for o in ovens),
        chefs=int(pizzeria.get("staff", {}).get("chefs", 0)),
        arrivals_per_hour=float(sim.get("arrivals", {}).get("per_hour", 0)),
        faults=list(sim.get("faults", []) or []),
        expect=dict(sim.get("expect", {}) or {}),
    )


def list_scenarios(scenarios_dir: Path) -> list[ScenarioInfo]:
    return [load_scenario(p) for p in sorted(scenarios_dir.glob("*.yaml"))]
