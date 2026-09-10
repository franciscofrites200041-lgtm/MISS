import json
from uuid import UUID

import httpx
import pytest

from app.actions import SpoterMassRejected, emit_agregar_nota
from app.spoter import SpoterClient, TokenCache


MASS_URL = "https://hub.spoter.com.ar/api/mass.json"


def _make_client(handler):
    """Wrap the handler para auto-responder /api/auth.json con un token válido,
    así los tests no necesitan pre-cargar el cache para cada sub-instance."""
    def wrapped(request):
        if request.url.path == "/api/auth.json":
            return httpx.Response(200, json={"success": True, "token": "AUTOTOK"})
        return handler(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    return SpoterClient(http, email="e@x", password="pw", cache=TokenCache()), http


async def test_success_returns_id_original_uuid():
    def handler(request):
        return httpx.Response(200, json={"success": True, "data": []})

    client, http = _make_client(handler)
    async with http:
        id_original = await emit_agregar_nota(
            client,
            mass_url=MASS_URL,
            root_instance="95",
            sub_instance="26434",
            phone="5492615617031",
            mensaje_nota="[Transcripción] hola",
            id_user="42",
            nombre_sugerido="Ale",
        )

    UUID(id_original)


async def test_body_shape_matches_mass_contract():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json={"success": True})

    client, http = _make_client(handler)
    async with http:
        id_original = await emit_agregar_nota(
            client,
            mass_url=MASS_URL,
            root_instance="95",
            sub_instance="26434",
            phone="5492615617031",
            mensaje_nota="hola",
            id_user="42",
            nombre_sugerido="Ale",
        )

    assert captured["url"] == MASS_URL
    body = captured["body"]
    assert body["instance"] == "95"
    assert body["phone"] == "5492615617031"
    assert body["data"] == {"instance": "26434"}
    assert body["id_original"] == id_original
    assert body["origen"] == "miss"
    assert body["_meta"] == {}

    actions = body["actions"]
    assert len(actions) == 1
    assert actions[0] == {
        "codigo": "agregar_nota",
        "mensaje_nota": "hola",
        "id_user": "42",
        "numero_sugerido": "5492615617031",
        "nombre_sugerido": "Ale",
    }


async def test_omits_nombre_sugerido_when_empty():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"success": True})

    client, http = _make_client(handler)
    async with http:
        await emit_agregar_nota(
            client,
            mass_url=MASS_URL,
            root_instance="95",
            sub_instance="26434",
            phone="123",
            mensaje_nota="x",
            id_user="42",
            nombre_sugerido="",
        )

    action = captured["body"]["actions"][0]
    assert "nombre_sugerido" not in action


async def test_uses_sub_instance_for_auth_token_selection():
    cache = TokenCache()
    cache.set("hub.spoter.com.ar", "26434", "SUB_TOKEN")
    cache.set("hub.spoter.com.ar", "95", "ROOT_TOKEN")

    captured = {}

    def handler(request):
        captured["csrf"] = request.headers.get("X-Csrf-Spoter")
        return httpx.Response(200, json={"success": True})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = SpoterClient(http, email="e@x", password="pw", cache=cache)
    async with http:
        await emit_agregar_nota(
            client,
            mass_url=MASS_URL,
            root_instance="95",
            sub_instance="26434",
            phone="123",
            mensaje_nota="x",
            id_user="42",
        )

    assert captured["csrf"] == "SUB_TOKEN"


async def test_raises_when_status_not_200():
    def handler(request):
        return httpx.Response(500, json={"success": True})

    client, http = _make_client(handler)
    async with http:
        with pytest.raises(SpoterMassRejected):
            await emit_agregar_nota(
                client, mass_url=MASS_URL,
                root_instance="95", sub_instance="26434",
                phone="123", mensaje_nota="x", id_user="42",
            )


async def test_raises_when_success_false():
    def handler(request):
        return httpx.Response(200, json={"success": False, "error": "bad"})

    client, http = _make_client(handler)
    async with http:
        with pytest.raises(SpoterMassRejected):
            await emit_agregar_nota(
                client, mass_url=MASS_URL,
                root_instance="95", sub_instance="26434",
                phone="123", mensaje_nota="x", id_user="42",
            )


async def test_raises_when_success_missing():
    def handler(request):
        return httpx.Response(200, json={"data": [{"nickname": "x"}]})

    client, http = _make_client(handler)
    async with http:
        with pytest.raises(SpoterMassRejected):
            await emit_agregar_nota(
                client, mass_url=MASS_URL,
                root_instance="95", sub_instance="26434",
                phone="123", mensaje_nota="x", id_user="42",
            )


async def test_raises_when_body_not_json():
    def handler(request):
        return httpx.Response(200, content=b"OK", headers={"content-type": "text/plain"})

    client, http = _make_client(handler)
    async with http:
        with pytest.raises(SpoterMassRejected):
            await emit_agregar_nota(
                client, mass_url=MASS_URL,
                root_instance="95", sub_instance="26434",
                phone="123", mensaje_nota="x", id_user="42",
            )


async def test_each_call_generates_unique_id_original():
    ids = []

    def handler(request):
        body = json.loads(request.content)
        ids.append(body["id_original"])
        return httpx.Response(200, json={"success": True})

    client, http = _make_client(handler)
    async with http:
        await emit_agregar_nota(
            client, mass_url=MASS_URL, root_instance="95", sub_instance="26434",
            phone="123", mensaje_nota="x", id_user="42",
        )
        await emit_agregar_nota(
            client, mass_url=MASS_URL, root_instance="95", sub_instance="26434",
            phone="123", mensaje_nota="x", id_user="42",
        )

    assert len(ids) == 2
    assert ids[0] != ids[1]
    UUID(ids[0])
    UUID(ids[1])
