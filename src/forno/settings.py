"""Application configuration — environment variables only, 12-factor.

One Pydantic `Settings` object, validated at startup; the process refuses to
boot on anything invalid or missing. See docs/configuration.md
"Runtime configuration". No config files, no profiles, no layered override
precedence.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _redact_database_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.password is None:
        return url
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    if parts.username:
        netloc = f"{parts.username}:***@{netloc}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FORNO_", case_sensitive=False)

    database_url: str
    kafka_bootstrap: str

    role: Literal["serve", "relay", "project"] = "serve"
    promise_estimator: str = "naive"
    promise_grace_seconds: int = 120
    # Domain seconds per wall-clock second (ADR-0009). 1 for a real kitchen;
    # the simulator runs at 60 and the API must be told the same number, or
    # every promise is computed in the wrong unit. /health/ready reports it
    # and the simulator refuses to start against a mismatch.
    time_scale: int = Field(default=1, ge=1)

    outbox_poll_ms: int = 200
    outbox_batch: int = 100

    consumer_max_retries: int = 5

    sse_queue_size: int = 256

    # /metrics port for worker roles (relay, project) that have no FastAPI
    # process of their own to expose it on. `serve` exposes /metrics on the
    # main HTTP port instead.
    metrics_port: int = 9100

    log_level: str = "INFO"
    tracing_enabled: bool = False

    def __repr__(self) -> str:  # pragma: no cover
        """Redacts anything that looks like a credential. See
        docs/configuration.md "Runtime configuration" (last paragraph)."""
        values = self.model_dump()
        values["database_url"] = _redact_database_url(values["database_url"])
        body = ", ".join(f"{key}={value!r}" for key, value in values.items())
        return f"Settings({body})"
