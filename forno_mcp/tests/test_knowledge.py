"""The MCP package copies a few facts from the domain because it may not
import `forno`. These tests pin the copies to the documents they came from."""

import re

import yaml

from forno_mcp import knowledge

from .conftest import REPO_ROOT


def test_order_transitions_match_the_documented_table():
    text = (REPO_ROOT / "docs/domain-model.md").read_text()
    table = dict(re.findall(r"OrderStatus\.(\w+):\s+frozenset\(\{([^}]*)\}\)", text))
    documented = {k: tuple(s.strip() for s in v.split(",")) for k, v in table.items()}
    assert documented == knowledge.ORDER_TRANSITIONS


def test_item_transitions_match_the_domain_code():
    text = (REPO_ROOT / "src/forno/domain/item.py").read_text()
    pairs = re.findall(r"ItemStatus\.(\w+): frozenset\(\{([^}]*)\}\)", text)
    code = {k: tuple(s.strip().removeprefix("ItemStatus.") for s in v.split(",")) for k, v in pairs}
    assert code == knowledge.ITEM_TRANSITIONS


def test_every_alert_rule_has_a_runbook_section():
    rules = yaml.safe_load((REPO_ROOT / "deploy/prometheus/alert_rules.yml").read_text())
    names = {r["alert"] for g in rules["groups"] for r in g["rules"]}
    assert names == set(knowledge.ALERT_RUNBOOK_SECTION)
    for heading in set(knowledge.ALERT_RUNBOOK_SECTION.values()):
        assert knowledge.runbook_section(REPO_ROOT, heading), heading


def test_doc_resources_exist():
    for path, _ in knowledge.DOC_RESOURCES.values():
        assert (REPO_ROOT / path).is_file(), path


def test_scenarios_load_with_capacity_and_expectations():
    scenarios = {s.name: s for s in knowledge.list_scenarios(REPO_ROOT / "scenarios")}
    assert {"busy-friday", "understaffed", "oven-down"} <= set(scenarios)
    oven_down = scenarios["oven-down"]
    assert oven_down.oven_slots == 14
    assert oven_down.faults[0]["action"] == "disable_oven"


def test_adrs_are_indexed():
    ids = [a["id"] for a in knowledge.list_adrs(REPO_ROOT)]
    assert "0008" in ids
    assert "external client" in knowledge.read_adr(REPO_ROOT, "8")
