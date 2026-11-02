"""Application-layer tests use fakes, never mocks — `InMemoryPublisher`,
`FrozenClock`, fake repositories. See CONTRIBUTING.md "Testing strategy":
"No mocks for code you own."

`PlaceOrder` itself doesn't fit that pattern: it queries `MenuItemModel`/
`OvenModel`/`OvenSlotModel` directly via `uow.session` (see
docs/adr/0010-abstraction-budget.md — no repository invented just for menu
lookups), so a pure in-memory fake would mean re-implementing a chunk of
SQLAlchemy's query API rather than testing real behaviour. It's covered
against a real Postgres instead: `test_price_change_does_not_alter_placed_order`-
style pricing/one-transaction behaviour by `tests/integration/test_order_repository.py`,
and the full request all the way through by `tests/e2e/test_order_placement.py`.
Idempotency-Key handling lives at the HTTP edge (`adapters/http/deps.py`), not
in this use case, so those two are e2e concerns too.
"""

import pytest


@pytest.mark.skip(
    reason=(
        "PlaceOrder needs a real DB session for menu/oven data — "
        "see tests/e2e/test_order_placement.py"
    )
)
def test_place_order_snapshots_prices_at_placement():
    pass


@pytest.mark.skip(
    reason=(
        "PlaceOrder needs a real DB session for menu/oven data — "
        "see tests/integration/test_order_repository.py"
    )
)
def test_place_order_writes_aggregate_and_outbox_in_one_transaction():
    """docs/architecture.md "Placing an order: the critical path"."""


@pytest.mark.skip(
    reason=(
        "Idempotency lives at the HTTP edge (deps.py), not in this use case — belongs in tests/e2e/"
    )
)
def test_double_submit_with_same_idempotency_key_replays_response():
    """docs/api.md "Idempotency"."""


@pytest.mark.skip(
    reason=(
        "Idempotency lives at the HTTP edge (deps.py), not in this use case — belongs in tests/e2e/"
    )
)
def test_idempotency_key_reused_with_different_body_is_reported_as_client_error():
    pass
