"""Shared fixtures for simulator tests."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def tiny_scenario_path() -> str:
    return str(FIXTURES / "tiny.yaml")
