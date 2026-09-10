import httpx
import pytest

from app.spoter import SpoterAuthError, SpoterClient, TokenCache


def _make_client(handler, cache=None):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SpoterClient(http, email="e@x", password="pw", cache=cache or TokenCache()), http


async def test_first_request_logs_in_and_sends_with_csrf_header():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/api/auth.json":
            return httpx.Response(200, json={"success": True, "token": "TOK123"})
        return httpx.Response(200, json={"data": [{"nickname": "Alice"}]})

    client, http = _make_client(handler)
    async with http:
        r = await client.request(
            "GET",
            "https://hub.spoter.com.ar/hynts/getdata.json?entity=info&instance=95&phone=123",
            instance="95",
        )

    assert r.status_code == 200
    assert r.json() == {"data": [{"nickname": "Alice"}]}
    assert len(seen) == 2
    assert seen[0].url.path == "/api/auth.json"
    assert seen[1].headers["X-Csrf-Spoter"] == "TOK123"


async def test_login_body_contains_email_instance_password():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/api/auth.json":
            return httpx.Response(200, json={"token": "T"})
        return httpx.Response(200)

    client, http = _make_client(handler)
    async with http:
        await client.request("GET", "https://hub.spoter.com.ar/x", instance="95")

    import json as _json
    body = _json.loads(seen[0].content)
    assert body == {"email": "e@x", "instance": "95", "password": "pw"}


async def test_cached_token_avoids_relogin():
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.path != "/api/auth.json"
        return httpx.Response(200, json={"ok": True})

    cache = TokenCache()
    cache.set("hub.spoter.com.ar", "95", "CACHED")
    client, http = _make_client(handler, cache=cache)
    async with http:
        r = await client.request("GET", "https://hub.spoter.com.ar/x", instance="95")

    assert r.status_code == 200
    assert len(seen) == 1
    assert seen[0].headers["X-Csrf-Spoter"] == "CACHED"


async def test_login_falls_back_to_form_when_json_gets_302():
    seen = []
    auth_calls = {"n": 0}

    def handler(request):
        seen.append(request)
        if request.url.path == "/api/auth.json":
            auth_calls["n"] += 1
            if auth_calls["n"] == 1:
                return httpx.Response(302, headers={"location": "/users/login"})
            return httpx.Response(200, json={"token": "TOK_FORM"})
        return httpx.Response(200, json={"ok": True})

    client, http = _make_client(handler)
    async with http:
        r = await client.request("GET", "https://hub.spoter.com.ar/x", instance="95")

    assert r.status_code == 200
    assert auth_calls["n"] == 2
    assert seen[0].headers["content-type"].startswith("application/json")
    assert seen[1].headers["content-type"].startswith("application/x-www-form-urlencoded")


async def test_login_never_follows_redirects():
    auth_calls = {"n": 0}

    def handler(request):
        if request.url.path == "/api/auth.json":
            auth_calls["n"] += 1
            return httpx.Response(302, headers={"location": "/users/login"})
        pytest.fail(f"login should not follow redirect; got request to {request.url}")

    client, http = _make_client(handler)
    async with http:
        with pytest.raises(SpoterAuthError):
            await client.request("GET", "https://hub.spoter.com.ar/x", instance="95")

    assert auth_calls["n"] == 2


async def test_401_triggers_relogin_and_retry():
    auth_calls = {"n": 0}
    data_calls = {"n": 0}

    def handler(request):
        if request.url.path == "/api/auth.json":
            auth_calls["n"] += 1
            return httpx.Response(200, json={"token": f"TOK{auth_calls['n']}"})
        data_calls["n"] += 1
        if data_calls["n"] == 1:
            return httpx.Response(401)
        return httpx.Response(200, json={"ok": True})

    client, http = _make_client(handler)
    async with http:
        r = await client.request("GET", "https://hub.spoter.com.ar/x", instance="95")

    assert r.status_code == 200
    assert auth_calls["n"] == 2
    assert data_calls["n"] == 2


async def test_403_triggers_relogin_and_retry():
    auth_calls = {"n": 0}
    data_calls = {"n": 0}

    def handler(request):
        if request.url.path == "/api/auth.json":
            auth_calls["n"] += 1
            return httpx.Response(200, json={"token": "TOK"})
        data_calls["n"] += 1
        if data_calls["n"] == 1:
            return httpx.Response(403)
        return httpx.Response(200, json={"ok": True})

    client, http = _make_client(handler)
    async with http:
        r = await client.request("GET", "https://hub.spoter.com.ar/x", instance="95")

    assert r.status_code == 200
    assert auth_calls["n"] == 2


async def test_302_to_login_triggers_relogin_and_retry():
    auth_calls = {"n": 0}
    data_calls = {"n": 0}

    def handler(request):
        if request.url.path == "/api/auth.json":
            auth_calls["n"] += 1
            return httpx.Response(200, json={"token": "TOK"})
        data_calls["n"] += 1
        if data_calls["n"] == 1:
            return httpx.Response(302, headers={"location": "/users/login"})
        return httpx.Response(200, json={"ok": True})

    client, http = _make_client(handler)
    async with http:
        r = await client.request("GET", "https://hub.spoter.com.ar/x", instance="95")

    assert r.status_code == 200


async def test_200_with_invalid_token_body_triggers_relogin_and_retry():
    auth_calls = {"n": 0}
    data_calls = {"n": 0}

    def handler(request):
        if request.url.path == "/api/auth.json":
            auth_calls["n"] += 1
            return httpx.Response(200, json={"token": "TOK"})
        data_calls["n"] += 1
        if data_calls["n"] == 1:
            return httpx.Response(200, json={"error": "invalid 'token' value."})
        return httpx.Response(200, json={"ok": True})

    client, http = _make_client(handler)
    async with http:
        r = await client.request("GET", "https://hub.spoter.com.ar/x", instance="95")

    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert auth_calls["n"] == 2


async def test_double_auth_failure_raises():
    def handler(request):
        if request.url.path == "/api/auth.json":
            return httpx.Response(200, json={"token": "TOK"})
        return httpx.Response(401)

    client, http = _make_client(handler)
    async with http:
        with pytest.raises(SpoterAuthError):
            await client.request("GET", "https://hub.spoter.com.ar/x", instance="95")


async def test_relogin_uses_the_same_instance_as_the_original_request():
    auth_bodies = []

    def handler(request):
        if request.url.path == "/api/auth.json":
            import json as _json
            body = _json.loads(request.content) if request.content else {}
            auth_bodies.append(body)
            return httpx.Response(200, json={"token": "TOK"})
        return httpx.Response(200)

    client, http = _make_client(handler)
    async with http:
        await client.request("GET", "https://hub.spoter.com.ar/x", instance="26434")

    assert auth_bodies[0]["instance"] == "26434"


def test_token_cache_persists_to_disk(tmp_path):
    path = tmp_path / "auth.json"

    a = TokenCache(path=path)
    a.set("hub.spoter.com.ar", "95", "TOKENA")

    b = TokenCache(path=path)
    assert b.get("hub.spoter.com.ar", "95") == "TOKENA"


def test_token_cache_invalidate_removes_from_disk(tmp_path):
    path = tmp_path / "auth.json"

    a = TokenCache(path=path)
    a.set("hub.spoter.com.ar", "95", "TOKENA")
    a.invalidate("hub.spoter.com.ar", "95")

    b = TokenCache(path=path)
    assert b.get("hub.spoter.com.ar", "95") is None


def test_token_cache_survives_corrupt_file(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text("{not json", encoding="utf-8")

    cache = TokenCache(path=path)
    assert cache.get("hub.spoter.com.ar", "95") is None
    cache.set("hub.spoter.com.ar", "95", "T")
    assert cache.get("hub.spoter.com.ar", "95") == "T"
