"""Environment-driven settings, mirroring the simulator's `FORNO_SIM_*` style."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path

# Deliberate stand-in for real auth, matching forno.adapters.http.deps.DEV_API_KEY
# (and simulator/__main__.py) — duplicated by necessity, this package cannot
# import `forno`. See docs/api.md "Conventions" ("On authentication").
_DEV_API_KEY = "dev"

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_API_URL = "http://localhost:8000"
_DEFAULT_PROMETHEUS_URL = "http://localhost:9090"


def _default_simulator_cmd() -> list[str]:
    return ["uv", "run", "--project", "simulator", "--quiet", "python", "-m", "simulator"]


@dataclass(frozen=True, slots=True)
class Settings:
    api_url: str = _DEFAULT_API_URL
    api_key: str = _DEV_API_KEY
    prometheus_url: str = _DEFAULT_PROMETHEUS_URL
    repo_root: Path = _REPO_ROOT
    simulator_cmd: list[str] = field(default_factory=_default_simulator_cmd)
    http_timeout_seconds: float = 5.0

    @property
    def scenarios_dir(self) -> Path:
        return self.repo_root / "scenarios"

    @property
    def docs_dir(self) -> Path:
        return self.repo_root / "docs"

    @classmethod
    def from_env(cls) -> Settings:
        cmd = os.environ.get("FORNO_MCP_SIMULATOR_CMD")
        return cls(
            api_url=os.environ.get("FORNO_MCP_API_URL", _DEFAULT_API_URL).rstrip("/"),
            api_key=os.environ.get("FORNO_MCP_API_KEY", _DEV_API_KEY),
            prometheus_url=os.environ.get(
                "FORNO_MCP_PROMETHEUS_URL", _DEFAULT_PROMETHEUS_URL
            ).rstrip("/"),
            repo_root=Path(os.environ.get("FORNO_MCP_REPO_ROOT", str(_REPO_ROOT))),
            simulator_cmd=shlex.split(cmd) if cmd else _default_simulator_cmd(),
        )
