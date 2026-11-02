from __future__ import annotations

from simulator.scenario import Scenario, SimulationConfig


def test_from_yaml_parses_a_valid_scenario(tiny_scenario_path: str) -> None:
    scenario = Scenario.from_yaml(tiny_scenario_path)

    assert scenario.name == "tiny"
    assert scenario.pizzeria.currency == "PLN"
    assert len(scenario.pizzeria.menu) == 2
    assert scenario.pizzeria.menu[0].sku == "margherita"
    assert scenario.simulation.seed == 1
    assert scenario.simulation.expect is not None
    assert scenario.simulation.expect.walkouts_max == 5


def test_expect_block_is_optional() -> None:
    config = SimulationConfig(
        seed=1,
        duration_minutes=0,
        time_scale=1,
        arrivals={"model": "poisson", "per_hour": 10},
        parties={"size_weights": {2: 1.0}, "takeaway_ratio": 0.0},
        behaviour={
            "deliberation_seconds": {"min": 10, "max": 20},
            "remake_rate": 0.0,
            "walkout_after_seconds": 300,
            "double_submit_rate": 0.0,
        },
    )

    assert config.expect is None
    assert config.duration_minutes == 0
