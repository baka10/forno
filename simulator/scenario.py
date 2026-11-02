"""Scenario file schema. See docs/configuration.md "Scenario schema" — the
two top-level blocks (`pizzeria`, `simulation`) are a design decision, not a
filing convention: `pizzeria` is pushed through the management API,
`simulation` never leaves this process.
"""

from __future__ import annotations

from decimal import Decimal  # noqa: TC003 -- pydantic needs it at runtime, not just typing
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


class OvenConfig(BaseModel):
    name: str
    slots: int
    bake_seconds: int


class StationConfig(BaseModel):
    count: int
    seconds_per_item: int | None = None


class MenuItemConfig(BaseModel):
    sku: str
    name: str
    price: Decimal
    vat: Decimal
    prep_seconds: int
    bake_seconds: int
    popularity: float


class TableConfig(BaseModel):
    number: int
    seats: int


class StaffConfig(BaseModel):
    cashiers: int
    chefs: int
    waiters: int


class PizzeriaConfig(BaseModel):
    """Domain capacity — the application must know this. See
    docs/configuration.md "The boundary"."""

    currency: str
    fire_together_window_seconds: int
    ovens: list[OvenConfig]
    stations: dict[str, StationConfig]
    staff: StaffConfig
    tables: list[TableConfig]
    menu: list[MenuItemConfig]


class RushConfig(BaseModel):
    at: str
    duration_minutes: int
    multiplier: float


class ArrivalsConfig(BaseModel):
    model: str
    per_hour: float
    rush: RushConfig | None = None


class PartiesConfig(BaseModel):
    size_weights: dict[int, float]
    takeaway_ratio: float


class DeliberationConfig(BaseModel):
    min: int
    max: int


class BehaviourConfig(BaseModel):
    deliberation_seconds: DeliberationConfig
    remake_rate: float
    walkout_after_seconds: int
    double_submit_rate: float


class ExpectRange(BaseModel):
    min: float
    max: float


class ExpectConfig(BaseModel):
    """The band the simulator asserts against. Turns a scenario from a demo
    into a regression test. See docs/configuration.md "Scenario schema"."""

    breach_rate: ExpectRange
    walkouts_max: int


class SimulationConfig(BaseModel):
    """Simulation behaviour — the application must never know this. See
    docs/configuration.md "The boundary"."""

    seed: int
    duration_minutes: int
    """0 means "run until the process is stopped" (SIGINT/SIGTERM) — used by
    the live-demo scenario. See docs/simulator.md "Running it"."""
    time_scale: int
    arrivals: ArrivalsConfig
    parties: PartiesConfig
    behaviour: BehaviourConfig
    expect: ExpectConfig | None = None
    """Declares the pass/fail band that turns a scenario into a regression
    test — see docs/configuration.md "Scenario schema". Omit for an
    open-ended live demo: there is no "end of run" to assert against."""
    faults: list[dict[str, Any]] = []
    menu_changes: list[dict[str, Any]] = []


class Scenario(BaseModel):
    name: str
    description: str
    pizzeria: PizzeriaConfig
    simulation: SimulationConfig

    @classmethod
    def from_yaml(cls, path: str) -> Scenario:
        with Path(path).open(encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
        return cls.model_validate(raw)
