from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.contacts import Contact, get_contact_by_phone
from app.spoter import SpoterClient, TokenCache


MASS_URL = "https://hub.spoter.com.ar/api/mass.json"


def _make_client(handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache = TokenCache()
    cache.set("hub.spoter.com.ar", "95", "TESTTOKEN")
    return SpoterClient(http, email="e@x", password="pw", cache=cache), http


async def test_returns_contact_when_nickname_present():
    def handler(request):
        return httpx.Response(200, json={
            "success": True,
            "data": {"nickname": "Ale Del Pozo", "fec_ult_mensaje": "2026-09-09"},
        })

    client, http = _make_client(handler)
    async with http:
        result = await get_contact_by_phone(
            client, mass_url=MASS_URL, instance="95", phone="5492615617031",
        )

    assert result == Contact(name="Ale Del Pozo")


async def test_returns_none_when_data_empty():
    def handler(request):
        return httpx.Response(200, json={"success": True, "data": {}})

    client, http = _make_client(handler)
    async with http:
        result = await get_contact_by_phone(
            client, mass_url=MASS_URL, instance="95", phone="123",
        )

    assert result is None


async def test_returns_none_when_data_absent():
    def handler(request):
        return httpx.Response(200, json={"success": False})

    client, http = _make_client(handler)
    async with http:
        result = await get_contact_by_phone(
            client, mass_url=MASS_URL, instance="95", phone="123",
        )

    assert result is None


async def test_returns_none_on_404():
    def handler(request):
        return httpx.Response(404)

    client, http = _make_client(handler)
    async with http:
        result = await get_contact_by_phone(
            client, mass_url=MASS_URL, instance="95", phone="123",
        )

    assert result is None


async def test_returns_none_when_response_is_not_json():
    def handler(request):
        return httpx.Response(200, content=b"<html>oops</html>", headers={"content-type": "text/html"})

    client, http = _make_client(handler)
    async with http:
        result = await get_contact_by_phone(
            client, mass_url=MASS_URL, instance="95", phone="123",
        )

    assert result is None


async def test_filters_error_prefix_as_junk():
    def handler(request):
        return httpx.Response(200, json={
            "success": True,
            "data": {"nickname": "Error: Account Not Exists"},
        })

    client, http = _make_client(handler)
    async with http:
        result = await get_contact_by_phone(
            client, mass_url=MASS_URL, instance="95", phone="123",
        )

    assert result is None


async def test_falls_back_to_nombres_when_nickname_missing():
    def handler(request):
        return httpx.Response(200, json={
            "success": True,
            "data": {"nombres": "Juan Perez"},
        })

    client, http = _make_client(handler)
    async with http:
        result = await get_contact_by_phone(
            client, mass_url=MASS_URL, instance="95", phone="123",
        )

    assert result == Contact(name="Juan Perez")


async def test_falls_back_to_first_name_and_alias():
    async def _run(field):
        def handler(request):
            return httpx.Response(200, json={"data": {field: "Fulano"}})
        client, http = _make_client(handler)
        async with http:
            return await get_contact_by_phone(
                client, mass_url=MASS_URL, instance="95", phone="123",
            )

    assert (await _run("first_name")) == Contact(name="Fulano")
    assert (await _run("alias")) == Contact(name="Fulano")


async def test_strips_whitespace_from_name():
    def handler(request):
        return httpx.Response(200, json={"data": {"nickname": "  Ale  "}})

    client, http = _make_client(handler)
    async with http:
        result = await get_contact_by_phone(
            client, mass_url=MASS_URL, instance="95", phone="123",
        )

    assert result == Contact(name="Ale")


async def test_request_uses_expected_path_and_query():
    seen = {}

    def handler(request):
        seen["url"] = request.url
        return httpx.Response(200, json={"data": {"nickname": "X"}})

    client, http = _make_client(handler)
    async with http:
        await get_contact_by_phone(
            client, mass_url=MASS_URL, instance="95", phone="5492615617031",
        )

    parsed = urlparse(str(seen["url"]))
    assert parsed.scheme == "https"
    assert parsed.netloc == "hub.spoter.com.ar"
    assert parsed.path == "/hynts/getdata.json"

    qs = parse_qs(parsed.query)
    assert qs["instance"] == ["95"]
    assert qs["entity"] == ["info"]
    assert qs["phone"] == ["5492615617031"]


async def test_returns_none_when_mass_url_is_malformed():
    def handler(request):
        pytest.fail("should not reach the network with a malformed mass_url")

    client, http = _make_client(handler)
    async with http:
        result = await get_contact_by_phone(
            client, mass_url="not a url", instance="95", phone="123",
        )

    assert result is None


async def test_data_as_list_is_ignored():
    def handler(request):
        # get_contactos devuelve `data: [...]`; para info esperamos dict.
        # Si Spoter llegara a devolver una lista, no la interpretamos como
        # un contacto único.
        return httpx.Response(200, json={"data": [{"nickname": "X"}]})

    client, http = _make_client(handler)
    async with http:
        result = await get_contact_by_phone(
            client, mass_url=MASS_URL, instance="95", phone="123",
        )

    assert result is None
