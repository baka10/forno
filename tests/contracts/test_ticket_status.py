"""The kitchen projector writes ticket statuses as plain strings; the HTTP
contract reads them back through `TicketStatus`. A status the projector can
write but the enum does not know turns every `GET /kitchen/tickets` that
includes that ticket into a 500 — which once stalled the whole simulated
kitchen after its first voided order. Pin the two together."""

import ast
import inspect
from uuid import uuid4

from forno.adapters.messaging import consumers
from forno.contracts.enums import TicketStatus
from forno.contracts.responses import KitchenTicketResponse


def _statuses_written_by_kitchen_projector() -> set[str]:
    tree = ast.parse(inspect.getsource(consumers.KitchenProjector))
    written = {"OPEN"}  # the status a ticket is born with in _on_order_placed
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_on_ticket_closed"
        ):
            status = node.args[-1]
            assert isinstance(status, ast.Constant)
            assert isinstance(status.value, str)
            written.add(status.value)
    return written


def test_every_ticket_status_the_projector_writes_is_part_of_the_contract():
    written = _statuses_written_by_kitchen_projector()
    assert written >= {"OPEN", "READY", "CANCELLED", "VOIDED"}
    assert written <= {s.value for s in TicketStatus}


def test_a_voided_ticket_serialises():
    response = KitchenTicketResponse(id=uuid4(), order_id=uuid4(), status="VOIDED", lines=[])
    assert response.status is TicketStatus.VOIDED
