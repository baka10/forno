"""Response bodies. See docs/api.md "Endpoints"."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel

from forno.contracts.enums import Channel, ItemStatus, OrderStatus, StationKind, TicketStatus


class MenuItemResponse(BaseModel):
    id: UUID
    sku: str
    name: str
    price: Decimal
    vat: Decimal
    available: bool


class OrderItemResponse(BaseModel):
    id: UUID
    menu_item_id: UUID
    name: str
    unit_price: Decimal
    status: ItemStatus
    fire_group: int
    remake_of: UUID | None


class OrderResponse(BaseModel):
    id: UUID
    status: OrderStatus
    channel: Channel
    table_number: int | None
    placed_at: datetime
    promised_at: datetime | None
    ready_at: datetime | None
    total_gross: Decimal
    currency: str
    items: list[OrderItemResponse]


class TicketLineResponse(BaseModel):
    order_item_id: UUID
    station: StationKind
    status: ItemStatus


class KitchenTicketResponse(BaseModel):
    id: UUID
    order_id: UUID
    status: TicketStatus
    lines: list[TicketLineResponse]


class StatsSummaryResponse(BaseModel):
    covers: int
    revenue: Decimal
    median_ready_seconds: float
    breach_rate: float
    remake_rate: float


class StatsTimeseriesPoint(BaseModel):
    bucket_start: datetime
    value: float


class StatsTimeseriesResponse(BaseModel):
    metric: str
    points: list[StatsTimeseriesPoint]
