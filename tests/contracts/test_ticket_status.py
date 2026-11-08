"""A ticket status the kitchen projector writes but the HTTP contract does not
know turns every `GET /kitchen/tickets` that includes that ticket into a 500.
That once stalled the whole simulated kitchen after its first voided order:
chefs find work only through that endpoint. The projector now takes
`TicketStatus` (mypy keeps the two in step); this pins the value it was
missing."""

from uuid import uuid4

from forno.contracts.enums import TicketStatus
from forno.contracts.responses import KitchenTicketResponse


def test_a_voided_ticket_serialises():
    response = KitchenTicketResponse(id=uuid4(), order_id=uuid4(), status="VOIDED", lines=[])
    assert response.status is TicketStatus.VOIDED
