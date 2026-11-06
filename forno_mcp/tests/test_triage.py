"""The runbook's triage tree, branch by branch."""

from forno_mcp import triage
from forno_mcp.snapshot import Snapshot

from .conftest import REPO_ROOT


def snap(**signals: float) -> Snapshot:
    s = Snapshot(taken_at="now", api={"status": "ok", "time_scale": 1})
    s.signals = {
        "outbox_lag_seconds": 0.5,
        "projection_lag_seconds": 0.2,
        "consumer_lag_messages": 0,
        "oldest_ticket_age_seconds": 200,
        "oven_utilisation": 0.5,
        "promise_met_ratio_5m": 0.99,
        "remake_ratio_30m": 0.02,
        "api_5xx_ratio_5m": 0.0,
        "dlq_messages_15m": 0,
    } | signals
    return s


def test_api_not_ready_is_a_software_problem():
    s = Snapshot(taken_at="now", api={"status": "not_ready", "detail": "Database not reachable"})
    d = triage.diagnose(s)
    assert d.category == "software"
    assert d.runbook_heading == "PostgresUnavailable"


def test_outbox_lag_is_checked_before_the_kitchen():
    d = triage.diagnose(snap(outbox_lag_seconds=45, oldest_ticket_age_seconds=2000))
    assert d.runbook_heading == "OutboxLagHigh / OutboxBacklogGrowing"


def test_projection_lag():
    d = triage.diagnose(snap(projection_lag_seconds=90))
    assert d.runbook_heading == "ProjectionLagHigh"


def test_full_ovens_with_old_tickets_is_capacity_not_a_defect():
    d = triage.diagnose(snap(oldest_ticket_age_seconds=1200, oven_utilisation=1.0))
    assert d.category == "capacity"
    assert "ovens are full" in d.verdict
    assert any("1200s" in step for step in d.path)


def test_old_tickets_with_idle_ovens_points_at_prep():
    d = triage.diagnose(snap(oldest_ticket_age_seconds=1200, oven_utilisation=0.3))
    assert "before the oven" in d.verdict


def test_remakes_win_over_capacity():
    d = triage.diagnose(snap(oldest_ticket_age_seconds=1200, remake_ratio_30m=0.2))
    assert d.category == "quality"


def test_breaches_without_backlog_blame_the_estimator():
    d = triage.diagnose(snap(promise_met_ratio_5m=0.7))
    assert d.category == "estimator"


def test_healthy():
    d = triage.diagnose(snap())
    assert d.category == "healthy"
    out = d.public(REPO_ROOT)
    assert out["runbook"] is None


def test_runbook_text_is_attached():
    out = triage.diagnose(snap(projection_lag_seconds=90)).public(REPO_ROOT)
    assert "Confirm" in out["runbook"]["text"] or "Means" in out["runbook"]["text"]


def test_without_prometheus_falls_back_to_api_data():
    s = Snapshot(taken_at="now", api={"status": "ok"}, unavailable=["prometheus"])
    s.ovens = [{"id": "deck-1", "active": True, "occupied": 8, "slots": 8}]
    s.orders = {"oldest_unready": {"placed_at": "2020-01-01T00:00:00Z"}}
    d = triage.diagnose(s)
    assert d.category == "capacity"
    assert d.data_gaps
