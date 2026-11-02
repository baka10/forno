"""RFC 9457 `application/problem+json`. See docs/api.md "Errors"."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ProblemDetail(BaseModel):
    # Extension members vary by problem type, e.g. `current_status` / `attempted`
    # on illegal-transition — see docs/api.md "Errors" for the worked example.
    model_config = ConfigDict(extra="allow")

    type: str
    title: str
    status: int
    detail: str
    instance: str
    request_id: str
