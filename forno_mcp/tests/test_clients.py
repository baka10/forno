"""Prometheus result shapes beyond the instant vector."""

import httpx
import pytest

from forno_mcp.clients import Prometheus

pytestmark = pytest.mark.asyncio


def prom_returning(settings, kind: str, result: object) -> Prometheus:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"status": "success", "data": {"resultType": kind, "result": result}}
        return httpx.Response(200, json=body)

    return Prometheus(settings, transport=httpx.MockTransport(handler))


async def test_scalar_result_is_one_unlabelled_value(settings):
    prom = prom_returning(settings, "scalar", [1_700_000_000, "2"])
    assert await prom.vector("1+1") == [({}, 2.0)]
    assert await prom.scalar("1+1") == 2.0


async def test_range_vector_is_refused_with_a_hint(settings):
    prom = prom_returning(settings, "matrix", [{"metric": {}, "values": [[0, "1"]]}])
    with pytest.raises(ValueError, match=r"rate\(metric\[5m\]\)"):
        await prom.vector("forno_outbox_pending[5m]")
