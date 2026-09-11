import httpx
import pytest

from app.spoter import SpoterAuthError, SpoterClient


def _make_client(handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SpoterClient(http), http


async def test_set_token_then_request_adds_csrf_header():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    client, http = _make_client(handler)
    client.set_token("hub.spoter.com.ar", "95", "TOK123")
    async with http:
        r = await client.request(
            "GET", "https://hub.spoter.com.ar/hynts/getdata.json?x=1", instance="95",
        )

    assert r.status_code == 200
    assert len(seen) == 1
    assert seen[0].headers["X-Csrf-Spoter"] == "TOK123"


async def test_missing_token_raises_auth_error():
    def handler(request):
        pytest.fail("should not reach network without token")

    client, http = _make_client(handler)
    async with http:
        with pytest.raises(SpoterAuthError):
            await client.request(
                "GET", "https://hub.spoter.com.ar/x", instance="95",
            )


async def test_tokens_are_scoped_per_host_and_instance():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200)

    client, http = _make_client(handler)
    client.set_token("hub.spoter.com.ar", "95", "T-A")
    client.set_token("hub.spoter.com.ar", "999", "T-B")
    client.set_token("other.host", "95", "T-C")
    async with http:
        await client.request("GET", "https://hub.spoter.com.ar/x", instance="95")
        await client.request("GET", "https://hub.spoter.com.ar/x", instance="999")
        await client.request("GET", "https://other.host/x", instance="95")

    assert seen[0].headers["X-Csrf-Spoter"] == "T-A"
    assert seen[1].headers["X-Csrf-Spoter"] == "T-B"
    assert seen[2].headers["X-Csrf-Spoter"] == "T-C"


async def test_set_token_overwrites_previous_value():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200)

    client, http = _make_client(handler)
    client.set_token("hub.spoter.com.ar", "95", "OLD")
    client.set_token("hub.spoter.com.ar", "95", "NEW")
    async with http:
        await client.request("GET", "https://hub.spoter.com.ar/x", instance="95")

    assert seen[0].headers["X-Csrf-Spoter"] == "NEW"
