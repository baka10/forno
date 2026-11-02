"""`X-Role: cashier` (+ `waiter` for delivery, `manager` for void — manager
can also do everything cashier/waiter can, see `deps.requires`). See
docs/api.md "Ordering — cashier" and "Service — waiter".

State transitions are `POST /…/{action}`, not `PATCH {status}` — see
docs/api.md "Why actions, not status patches".
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from forno.adapters.db.unit_of_work import SqlAlchemyUnitOfWork
from forno.adapters.http.deps import (
    Role,
    hash_request_body,
    idempotency_key,
    load_idempotent_response,
    requires,
    store_idempotent_response,
)
from forno.adapters.http.wiring import get_clock, get_estimator, get_uow
from forno.application.use_cases.orders import (
    CancelOrder,
    CloseOrder,
    DeliverItem,
    GetOrder,
    ListOrders,
    PlaceOrder,
    PlaceOrderLine,
    VoidOrder,
)
from forno.contracts.enums import OrderStatus
from forno.contracts.mapping import order_to_response
from forno.contracts.requests import PlaceOrderRequest, VoidOrderRequest
from forno.contracts.responses import OrderResponse
from forno.domain.clock import Clock
from forno.domain.ids import OrderId
from forno.domain.ports import PromiseEstimator
from forno.observability import metrics
from forno.observability.context import get_correlation_id

router = APIRouter(tags=["orders"])

# The schema carries no currency column — see adapters/db/repositories.py.
_CURRENCY = "PLN"


@router.post(
    "/orders",
    status_code=201,
    dependencies=[requires(Role.CASHIER)],
    summary="Place an order",
    description=(
        "Snapshots menu prices onto the order, computes `promised_at` via "
        "the configured `PromiseEstimator`, and persists the order + its "
        "first domain events in one transaction. Safe to retry: send an "
        "`Idempotency-Key` header and a duplicate submit (e.g. a cashier "
        "double-tap) replays the original `201` response instead of "
        "creating a second order. See docs/architecture.md "
        '"Placing an order: the critical path".'
    ),
)
async def place_order(
    request: Request,
    response: Response,
    body: PlaceOrderRequest,
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    estimator: PromiseEstimator = Depends(get_estimator),
    clock: Clock = Depends(get_clock),
    idem_key: str | None = Depends(idempotency_key),
) -> OrderResponse:
    correlation_id = get_correlation_id() or "-"
    request_hash = hash_request_body(await request.body())

    if idem_key:
        async with uow:
            cached = await load_idempotent_response(uow.session, idem_key, request_hash)
        if cached is not None:
            status_code, stored_body = cached
            response.status_code = status_code
            response.headers["Idempotent-Replay"] = "true"
            metrics.idempotency_replays_total.labels(endpoint="/orders").inc()
            return OrderResponse(**stored_body)

    lines = [
        PlaceOrderLine(
            menu_item_id=line.menu_item_id, quantity=line.quantity, fire_group=line.fire_group
        )
        for line in body.lines
    ]
    order = await PlaceOrder(uow, estimator, clock).execute(
        channel=body.channel,
        table_number=body.table_number,
        lines=lines,
        correlation_id=correlation_id,
    )
    result = order_to_response(order, _CURRENCY)

    if idem_key:
        async with uow:
            await store_idempotent_response(
                uow.session, idem_key, request_hash, 201, result.model_dump(mode="json")
            )

    return result


@router.get(
    "/orders/{order_id}",
    summary="Get an order",
    description="Full state including per-item status. No role required.",
)
async def get_order(order_id: UUID, uow: SqlAlchemyUnitOfWork = Depends(get_uow)) -> OrderResponse:
    order = await GetOrder(uow).execute(OrderId(order_id))
    return order_to_response(order, _CURRENCY)


@router.get(
    "/orders",
    summary="List orders",
    description="Cursor paginated by `placed_at`. No role required.",
)
async def list_orders(
    status: OrderStatus | None = None,
    table: int | None = None,
    since: str | None = None,
    limit: int = 50,
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> list[OrderResponse]:
    orders = await ListOrders(uow).execute(
        status=status.value if status else None, table=table, since=since, limit=limit
    )
    return [order_to_response(order, _CURRENCY) for order in orders]


@router.post(
    "/orders/{order_id}/cancel",
    dependencies=[requires(Role.CASHIER)],
    summary="Cancel an order",
    description=(
        "Legal any time before an item enters the oven — dough is a "
        "committed physical cost past that point. Returns `409` once any "
        "item has entered the oven (baking, ready, or scrapped after "
        "baking); that response means let service finish and `void` "
        "instead of retrying. See docs/domain-model.md invariant #8."
    ),
)
async def cancel_order(
    order_id: UUID,
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    clock: Clock = Depends(get_clock),
) -> OrderResponse:
    order = await CancelOrder(uow, clock).execute(OrderId(order_id), get_correlation_id() or "-")
    return order_to_response(order, _CURRENCY)


@router.post(
    "/orders/{order_id}/void",
    dependencies=[requires(Role.MANAGER)],
    summary="Void an order (manager)",
    description=(
        "The write-off for food that was made but will not be paid for — a "
        "walkout after the dough went in, a complaint after service. Legal "
        "from `IN_KITCHEN` on; before that use `cancel`. Distinct from "
        "`cancel`: the production record stays, pizzas still in the oven "
        'free their slots. See docs/domain-model.md "Order lifecycle".'
    ),
)
async def void_order(
    order_id: UUID,
    body: VoidOrderRequest,
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    clock: Clock = Depends(get_clock),
) -> OrderResponse:
    order = await VoidOrder(uow, clock).execute(
        OrderId(order_id), body.reason, get_correlation_id() or "-"
    )
    return order_to_response(order, _CURRENCY)


@router.post(
    "/orders/{order_id}/close",
    dependencies=[requires(Role.CASHIER)],
    summary="Close an order / finalise the receipt",
)
async def close_order(
    order_id: UUID,
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    clock: Clock = Depends(get_clock),
) -> OrderResponse:
    order = await CloseOrder(uow, clock).execute(OrderId(order_id), get_correlation_id() or "-")
    return order_to_response(order, _CURRENCY)


@router.post(
    "/orders/{order_id}/items/{item_id}/delivered",
    dependencies=[requires(Role.WAITER)],
    summary="Mark an item delivered",
    description=(
        "Idempotent — calling it twice on an already-delivered item is a "
        "no-op, not an error. Double-delivery is invariant #1 in the "
        "simulator's shadow ledger. Returns `409` until the order itself "
        "is `READY` (invariant #9: fire-together means the first pizza "
        "waits for the rest)."
    ),
)
async def deliver_item(
    order_id: UUID,
    item_id: UUID,
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    clock: Clock = Depends(get_clock),
) -> OrderResponse:
    order = await DeliverItem(uow, clock).execute(
        OrderId(order_id), str(item_id), get_correlation_id() or "-"
    )
    return order_to_response(order, _CURRENCY)
