"""docs/runbook.md "Triage: the first sixty seconds", as code.

The runbook's key question is "is the system broken, or are there more
customers than chefs?". This walks the same decision tree, records every
branch it took with the value that decided it, and points at the runbook
section to read next — so the agent's conclusion is auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from forno_mcp.knowledge import runbook_section
from forno_mcp.snapshot import SLO_TARGET

if TYPE_CHECKING:
    from pathlib import Path

    from forno_mcp.snapshot import Snapshot

# Thresholds are the alert thresholds in deploy/prometheus/alert_rules.yml,
# so "elevated" here means the same thing it means to Alertmanager.
OUTBOX_LAG_HIGH_S = 30
PROJECTION_LAG_HIGH_S = 60
CONSUMER_LAG_HIGH_MSGS = 500
TICKET_AGE_HIGH_S = 900
OVEN_SATURATED = 0.9
REMAKE_RATIO_HIGH = 0.1
API_5XX_HIGH = 0.01


@dataclass
class Diagnosis:
    verdict: str
    category: str  # "software" | "capacity" | "estimator" | "quality" | "healthy" | "unknown"
    runbook_heading: str | None
    path: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)

    def public(self, repo_root: Path) -> dict[str, Any]:
        section = runbook_section(repo_root, self.runbook_heading) if self.runbook_heading else None
        return {
            "verdict": self.verdict,
            "category": self.category,
            "decision_path": self.path,
            "recommended_actions": self.actions,
            "runbook": (
                {"heading": self.runbook_heading, "text": section} if self.runbook_heading else None
            ),
            "data_gaps": self.data_gaps,
        }


def _oven_utilisation(snap: Snapshot) -> float | None:
    value = snap.signals.get("oven_utilisation")
    if value is not None:
        return value
    active = [o for o in snap.ovens if o["active"]]
    slots = sum(o["slots"] for o in active)
    return sum(o["occupied"] for o in active) / slots if slots else None


def _oldest_unready_age(snap: Snapshot) -> float | None:
    value = snap.signals.get("oldest_ticket_age_seconds")
    if value is not None:
        return value
    oldest = (snap.orders or {}).get("oldest_unready")
    if not oldest:
        return None
    placed = datetime.fromisoformat(oldest["placed_at"])
    return (datetime.now(UTC) - placed).total_seconds()


def _kitchen_branch(d: Diagnosis, snap: Snapshot, age: float) -> Diagnosis:
    util = _oven_utilisation(snap)
    remakes = snap.signals.get("remake_ratio_30m")
    d.runbook_heading = "PromiseBudgetBurn (Fast / Slow)"
    d.path.append(f"oldest ticket age {age:.0f}s > {TICKET_AGE_HIGH_S}s: the kitchen is behind")
    if remakes is not None and remakes > REMAKE_RATIO_HIGH:
        d.path.append(f"remake ratio {remakes:.1%} > {REMAKE_RATIO_HIGH:.0%}")
        d.verdict = "Quality problem upstream of the software: remakes inherit promises and breach."
        d.category = "quality"
        d.actions.append("Not a software response. Escalate to the kitchen.")
        return d
    if util is not None and util >= OVEN_SATURATED:
        d.path.append(f"oven utilisation {util:.0%} >= {OVEN_SATURATED:.0%}")
        d.verdict = "Genuine capacity limit: the ovens are full. The software is fine."
        d.category = "capacity"
        d.actions += [
            "Add oven capacity or bring an inactive oven back (set_oven_active).",
            "Add chefs for the shift (POST /shifts); the estimator picks it up on the next order.",
        ]
        return d
    util_txt = f"{util:.0%}" if util is not None else "unknown"
    d.path.append(f"oven utilisation {util_txt} is low while tickets are old")
    d.verdict = "Items are stuck before the oven: prep is the bottleneck, not the ovens."
    d.category = "capacity"
    d.actions += [
        'Check forno_item_stage_seconds{stage="prep"} (query_metric).',
        "Usually a prep station or a chef short; add staff via POST /shifts.",
    ]
    return d


def diagnose(snap: Snapshot) -> Diagnosis:  # noqa: PLR0911, PLR0915 -- one return per runbook branch, kept in the runbook's order
    d = Diagnosis(verdict="", category="unknown", runbook_heading="Triage: the first sixty seconds")
    if "prometheus" in snap.unavailable:
        d.data_gaps.append(
            "Prometheus unreachable: pipeline lag checks skipped, kitchen checks use the API only."
        )

    if snap.api.get("status") != "ok":
        d.path.append(f"/health/ready is not green: {snap.api.get('detail')}")
        d.verdict = "The API is not ready: Postgres is unreachable or the API is down."
        d.category = "software"
        d.runbook_heading = "PostgresUnavailable"
        d.actions.append("docker compose ps; docker compose logs api postgres")
        return d
    d.path.append("/health/ready is green")

    sig = snap.signals
    outbox = sig.get("outbox_lag_seconds")
    if outbox is not None and outbox > OUTBOX_LAG_HIGH_S:
        d.path.append(f"outbox lag {outbox:.0f}s > {OUTBOX_LAG_HIGH_S}s")
        d.verdict = "Publishing is broken: the outbox relay is not draining. Displays go stale."
        d.category = "software"
        d.runbook_heading = "OutboxLagHigh / OutboxBacklogGrowing"
        d.actions.append("docker compose logs outbox-relay; check Redpanda health.")
        return d
    d.path.append(f"outbox lag {outbox if outbox is not None else 'unknown'}s is normal")

    proj, consumer = sig.get("projection_lag_seconds"), sig.get("consumer_lag_messages")
    if (proj is not None and proj > PROJECTION_LAG_HIGH_S) or (
        consumer is not None and consumer > CONSUMER_LAG_HIGH_MSGS
    ):
        d.path.append(f"projection lag {proj}s / consumer lag {consumer} msgs elevated")
        d.verdict = "Consuming is broken: projectors are behind, the KDS shows the past."
        d.category = "software"
        d.runbook_heading = "ProjectionLagHigh"
        d.actions.append("docker compose logs projector-kitchen projector-stats")
        return d
    d.path.append("projection and consumer lag are normal")

    age = _oldest_unready_age(snap)
    if age is not None and age > TICKET_AGE_HIGH_S:
        return _kitchen_branch(d, snap, age)
    d.path.append(f"oldest unready work {age:.0f}s is normal" if age is not None else "no backlog")

    met = sig.get("promise_met_ratio_5m")
    if met is not None and met < SLO_TARGET:
        d.path.append(f"promise SLI {met:.1%} < {SLO_TARGET:.0%} with normal ticket age")
        d.verdict = "The estimator is promising times the kitchen was never going to meet."
        d.category = "estimator"
        d.runbook_heading = "PromiseBudgetBurn (Fast / Slow)"
        d.actions.append("Long term: a better PromiseEstimator (ADR-0007). Do not just add grace.")
        return d

    api5xx, dlq = sig.get("api_5xx_ratio_5m"), sig.get("dlq_messages_15m")
    if api5xx is not None and api5xx > API_5XX_HIGH:
        d.path.append(f"API 5xx ratio {api5xx:.1%} > {API_5XX_HIGH:.0%}")
        d.verdict = "The API is erroring."
        d.category = "software"
        d.runbook_heading = "ApiErrorRate"
        return d
    if dlq:
        d.path.append(f"{dlq:.0f} message(s) dead-lettered in the last 15 minutes")
        d.verdict = "Something needs a human: events landed in the DLQ."
        d.category = "software"
        d.runbook_heading = "DLQNotEmpty / PoisonEventDetected"
        return d

    d.verdict = "Healthy: no pipeline lag, no backlog, promises being met."
    d.category = "healthy"
    d.runbook_heading = None
    return d
