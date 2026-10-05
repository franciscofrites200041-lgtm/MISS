from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx

from app.operators import OperatorDirectory
from app.spoter import SpoterClient
from app.tools_store import ToolsStore


MASS_URL = "https://hub.spoter.com.ar/api/mass.json"


def _client(handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = SpoterClient(http)
    client.set_token("hub.spoter.com.ar", "95", "TESTTOKEN")
    return client, http


async def test_fetches_and_persists_directory_by_root_instance(tmp_path):
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, json={"data": [
            {"id": "10", "nombre": "María Pérez"},
            {"id": "11", "nombre": "  Juan Gómez  "},
        ]})

    store = ToolsStore(tmp_path / "tools.db")
    await store.init()
    client, http = _client(handler)
    directory = OperatorDirectory(store, ttl=timedelta(days=7))
    async with http:
        assert await directory.get_name(client, mass_url=MASS_URL, instance="95", operator_id="10") == "María Pérez"

    snapshot, _ = await store.load_operator_snapshot("95")
    assert snapshot == {"10": "María Pérez", "11": "Juan Gómez"}
    query = parse_qs(urlparse(str(seen[0])).query)
    assert query == {"entity": ["operadores"], "instance": ["95"]}


async def test_uses_fresh_persisted_snapshot_without_request(tmp_path):
    def handler(request):
        raise AssertionError("fresh snapshot must prevent endpoint request")

    store = ToolsStore(tmp_path / "tools.db")
    await store.init()
    await store.save_operator_snapshot("95", {"10": "María Pérez"})
    client, http = _client(handler)
    directory = OperatorDirectory(store, ttl=timedelta(days=7))
    async with http:
        assert await directory.get_name(client, mass_url=MASS_URL, instance="95", operator_id="10") == "María Pérez"


async def test_uses_stale_snapshot_when_refresh_fails(tmp_path):
    def handler(request):
        return httpx.Response(503)

    store = ToolsStore(tmp_path / "tools.db")
    await store.init()
    await store.save_operator_snapshot(
        "95", {"10": "María Pérez"},
        fetched_at=datetime.now(timezone.utc) - timedelta(days=8),
    )
    client, http = _client(handler)
    directory = OperatorDirectory(store, ttl=timedelta(days=7))
    async with http:
        assert await directory.get_name(client, mass_url=MASS_URL, instance="95", operator_id="10") == "María Pérez"


async def test_returns_none_for_malformed_or_missing_operator(tmp_path):
    store = ToolsStore(tmp_path / "tools.db")
    await store.init()
    client, http = _client(lambda request: httpx.Response(200, json={"data": [{"id": 10}]}))
    directory = OperatorDirectory(store, ttl=timedelta(days=7))
    async with http:
        assert await directory.get_name(client, mass_url="invalid", instance="95", operator_id="10") is None
        assert await directory.get_name(client, mass_url=MASS_URL, instance="95", operator_id="99") is None
