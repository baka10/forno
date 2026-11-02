"""Request bodies. Money is string-encoded decimals plus explicit currency,
never a float in JSON — see docs/api.md "Conventions"."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from forno.contracts.enums import Channel, RemakeReason, StationKind


class CreateMenuItemRequest(BaseModel):
    sku: str = Field(examples=["MARG"])
    name: str = Field(examples=["Margherita"])
    price: Decimal = Field(examples=["32.00"])
    vat: Decimal = Field(examples=["0.08"], description="VAT rate, e.g. 0.08 for 8%.")
    prep_seconds: int = Field(examples=[60])
    bake_seconds: int = Field(examples=[300])


class UpdateMenuItemRequest(BaseModel):
    price: Decimal | None = Field(default=None, examples=["34.00"])
    available: bool | None = None


class CreateStationRequest(BaseModel):
    kind: StationKind
    name: str = Field(examples=["deck-1"], description="Unique station/oven id.")
    slots: int | None = Field(
        default=None, examples=[6], description="Oven slots, or prep/pass capacity."
    )
    bake_seconds: int | None = Field(default=None, examples=[300], description="Ovens only.")


class UpdateStationRequest(BaseModel):
    active: bool = Field(description="False takes an oven out of service; True brings it back.")


class CreateTableRequest(BaseModel):
    number: int = Field(examples=[7])
    seats: int = Field(examples=[4])


class CreateShiftRequest(BaseModel):
    """Staff on duty. Captured for the (not-yet-implemented) capacity-aware
    CP-SAT estimator — see docs/adr/0007-cp-sat-promise-estimator-seam.md.
    `NaiveEstimator`, the default today, does not read this."""

    cashiers: int = Field(examples=[1])
    chefs: int = Field(examples=[3])
    waiters: int = Field(examples=[2])


class OrderLineRequest(BaseModel):
    menu_item_id: str = Field(description="A menu item `id` from `GET /menu`.")
    quantity: int = Field(examples=[2], gt=0)
    fire_group: int = Field(
        examples=[1],
        description="Items sharing a fire_group must reach READY together. "
        'See docs/domain-model.md "Fire-together".',
    )


class PlaceOrderRequest(BaseModel):
    channel: Channel
    table_number: int | None = Field(default=None, examples=[7])
    lines: list[OrderLineRequest] = Field(min_length=1)


class VoidOrderRequest(BaseModel):
    reason: str = Field(examples=["Customer complaint — comped after service."])


class ScrapItemRequest(BaseModel):
    reason: RemakeReason


class SeatTableRequest(BaseModel):
    party_size: int = Field(examples=[4], gt=0)
