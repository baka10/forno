"""Identifier types.

`NewType` so it is impossible to pass a table where an order belongs — free at
runtime, caught by `mypy --strict`. See docs/domain-model.md "Money" (last
paragraph) and docs/adr/0010-abstraction-budget.md.
"""

from __future__ import annotations

from typing import NewType
from uuid import UUID

OrderId = NewType("OrderId", UUID)
OrderItemId = NewType("OrderItemId", UUID)
MenuItemId = NewType("MenuItemId", UUID)
TableId = NewType("TableId", int)  # table "number" in the API — see docs/api.md
OvenId = NewType("OvenId", str)
StationId = NewType("StationId", str)
KitchenTicketId = NewType("KitchenTicketId", UUID)
