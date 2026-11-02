"""Small wire-shape types shared across actors. Kept out of `scenario.py`
(the YAML schema) and out of `forno.contracts` (the simulator may not import
`forno` — see importlinter.ini `simulator-is-external`)."""

from __future__ import annotations

from enum import StrEnum


class Channel(StrEnum):
    """Must match `forno.domain.order.Channel`'s wire values exactly — the
    simulator sends these as plain JSON strings; the server validates them
    against its own enum. See docs/api.md "Ordering — cashier"."""

    DINE_IN = "dine_in"
    TAKEAWAY = "takeaway"
