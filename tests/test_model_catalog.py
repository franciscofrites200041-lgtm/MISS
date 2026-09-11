import httpx

from app.model_catalog import CatalogResult, ModelCatalog
from app.tools_store import ToolsStore


async def test_snapshot_roundtrip(tmp_path):
    store = ToolsStore(tmp_path / "tools.db")
    await store.init()
    payload = [{"id": "google/gemini-2.5-flash", "name": "Gemini 2.5 Flash"}]
    await store.save_model_snapshot("image", payload)
    assert await store.load_model_snapshot("image") == payload


async def test_snapshot_missing_kind_returns_none(tmp_path):
    store = ToolsStore(tmp_path / "tools.db")
    await store.init()
    assert await store.load_model_snapshot("audio") is None


async def test_snapshot_overwrites_previous_value(tmp_path):
    store = ToolsStore(tmp_path / "tools.db")
    await store.init()
    await store.save_model_snapshot("image", [{"id": "a", "name": "alpha"}])
    await store.save_model_snapshot("image", [{"id": "b", "name": "beta"}])
    assert await store.load_model_snapshot("image") == [{"id": "b", "name": "beta"}]


async def test_snapshot_survives_new_store_instance(tmp_path):
    payload = [{"id": "openai/whisper-1", "name": "Whisper"}]
    first = ToolsStore(tmp_path / "tools.db")
    await first.init()
    await first.save_model_snapshot("audio", payload)
    second = ToolsStore(tmp_path / "tools.db")
    await second.init()
    assert await second.load_model_snapshot("audio") == payload


MODELS_PAYLOAD = {
    "data": [
        {"id": "openai/gpt-4o", "name": "GPT-4o",
         "architecture": {"input_modalities": ["text", "image"]}},
        {"id": "claude/claude-3.5-sonnet", "name": "Claude 3.5 Sonnet",
         "architecture": {"input_modalities": ["text", "image"]}},
        {"id": "google/gemini-2.5-flash", "name": "Gemini 2.5 Flash",
         "architecture": {"input_modalities": ["text", "image", "file", "audio"]}},
        {"id": "openai/gpt-4o-mini", "name": "GPT-4o Mini",
         "architecture": {"input_modalities": ["text"]}},
        {"id": "anthropic/claude-sonnet-4", "name": "Claude Sonnet 4",
         "architecture": {"input_modalities": ["text", "file"]}},
    ]
}


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_image_filter_returns_only_models_with_image_input():
    def handler(request):
        assert request.url.path == "/api/v1/models"
        assert b"output_modalities" not in request.url.query
        return httpx.Response(200, json=MODELS_PAYLOAD)

    http = _client(handler)
    async with http:
        result = await ModelCatalog(http=http, api_key="sk", store=None).get("image")

    assert result.source == "live"
    assert [m["id"] for m in result.models] == [
        "claude/claude-3.5-sonnet", "google/gemini-2.5-flash", "openai/gpt-4o",
    ]


async def test_document_filter_returns_only_models_with_file_input():
    http = _client(lambda req: httpx.Response(200, json=MODELS_PAYLOAD))
    async with http:
        result = await ModelCatalog(http=http, api_key="sk", store=None).get("document")

    assert [m["id"] for m in result.models] == [
        "anthropic/claude-sonnet-4", "google/gemini-2.5-flash",
    ]


async def test_audio_uses_transcription_modality_filter():
    def handler(request):
        assert request.url.path == "/api/v1/models"
        assert request.url.query == b"output_modalities=transcription"
        return httpx.Response(200, json={
            "data": [
                {"id": "openai/whisper-1", "name": "Whisper"},
                {"id": "openai/whisper-large-v3", "name": "Whisper Large V3"},
            ]
        })

    http = _client(handler)
    async with http:
        result = await ModelCatalog(http=http, api_key="sk", store=None).get("audio")

    assert result.source == "live"
    assert [m["id"] for m in result.models] == [
        "openai/whisper-1", "openai/whisper-large-v3",
    ]


async def test_uses_authorization_header_when_key_present():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=MODELS_PAYLOAD)

    http = _client(handler)
    async with http:
        await ModelCatalog(http=http, api_key="sk-or-abc", store=None).get("image")

    assert seen[0].headers["authorization"] == "Bearer sk-or-abc"


async def test_skips_authorization_when_key_empty():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=MODELS_PAYLOAD)

    http = _client(handler)
    async with http:
        await ModelCatalog(http=http, api_key="", store=None).get("image")

    assert "authorization" not in seen[0].headers


async def test_cached_live_result_within_ttl_does_not_refetch():
    count = {"n": 0}

    def handler(request):
        count["n"] += 1
        return httpx.Response(200, json=MODELS_PAYLOAD)

    http = _client(handler)
    async with http:
        catalog = ModelCatalog(http=http, api_key="sk", store=None)
        first = await catalog.get("image")
        second = await catalog.get("image")

    assert count["n"] == 1
    assert first.models == second.models
    assert second.source == "cache"


async def test_fallback_to_stale_cache_when_refetch_fails():
    state = {"fails": False}

    def handler(request):
        if state["fails"]:
            return httpx.Response(500, json={"error": {"message": "boom"}})
        return httpx.Response(200, json=MODELS_PAYLOAD)

    http = _client(handler)
    async with http:
        # ttl=0 fuerza a considerar el cache vencido → hace refetch.
        catalog = ModelCatalog(http=http, api_key="sk", store=None, ttl_seconds=0)
        first = await catalog.get("image")
        state["fails"] = True
        second = await catalog.get("image")

    assert first.source == "live"
    assert second.source == "stale"
    assert second.models == first.models


async def test_returns_none_when_no_cache_and_fetch_fails():
    def handler(request):
        return httpx.Response(500, json={"error": {"message": "boom"}})

    http = _client(handler)
    async with http:
        catalog = ModelCatalog(http=http, api_key="sk", store=None, ttl_seconds=0)
        assert await catalog.get("image") is None


async def test_snapshot_fallback_when_no_cache(tmp_path):
    store = ToolsStore(tmp_path / "tools.db")
    await store.init()
    await store.save_model_snapshot("image", [{"id": "google/gemini-2.5-flash", "name": "Gemini"}])

    def handler(request):
        return httpx.Response(500, json={"error": {"message": "boom"}})

    http = _client(handler)
    async with http:
        catalog = ModelCatalog(http=http, api_key="sk", store=store, ttl_seconds=0)
        result = await catalog.get("image")

    assert result.source == "snapshot"
    assert result.models == [{"id": "google/gemini-2.5-flash", "name": "Gemini"}]


async def test_persists_live_snapshot_to_db(tmp_path):
    store = ToolsStore(tmp_path / "tools.db")
    await store.init()

    http = _client(lambda req: httpx.Response(200, json=MODELS_PAYLOAD))
    async with http:
        await ModelCatalog(http=http, api_key="sk", store=store).get("image")

    snapshot = await store.load_model_snapshot("image")
    assert snapshot and any(m["id"] == "openai/gpt-4o" for m in snapshot)
