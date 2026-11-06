from __future__ import annotations

from pathlib import Path

import pytest

from forno_mcp.config import Settings

from .fakes import FakeForno, FakePrometheus

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def forno() -> FakeForno:
    return FakeForno()


@pytest.fixture
def prom() -> FakePrometheus:
    return FakePrometheus()


@pytest.fixture
def settings() -> Settings:
    return Settings(repo_root=REPO_ROOT)
