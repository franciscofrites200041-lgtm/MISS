import base64
import json

import httpx
import pytest

from app.transcription import Transcription, TranscriptionError, transcribe, transcribe_bytes


AUDIO_BYTES = b"\xff\xfb\x90\x00fake mp3 bytes"
OPENROUTER_URL = "https://openrouter.ai/api/v1/audio/transcriptions"


def _handler_factory(*, audio_bytes=AUDIO_BYTES, audio_content_type="audio/mpeg",
                    openrouter_response=None, openrouter_status=200,
                    audio_status=200, seen=None):
    if openrouter_response is None:
        openrouter_response = {
            "text": "hola que tal",
            "usage": {"seconds": 3.2, "cost": 0.00042},
        }

    def handler(request):
        if seen is not None:
            seen.append(request)
        if request.url.host == "openrouter.ai":
            return httpx.Response(openrouter_status, json=openrouter_response)
        headers = {}
        if audio_content_type:
            headers["content-type"] = audio_content_type
        return httpx.Response(audio_status, content=audio_bytes, headers=headers)

    return handler


async def test_happy_path_returns_transcription_with_usage():
    http = httpx.AsyncClient(transport=httpx.MockTransport(_handler_factory()))
    async with http:
        result = await transcribe(
            "https://hub.spoter.com.ar/audio/1753",
            http=http,
            api_key="sk-or-fake",
        )

    assert isinstance(result, Transcription)
    assert result.text == "hola que tal"
    assert result.duration_seconds == 3.2
    assert result.cost_usd == 0.00042
    assert result.model == "openai/whisper-large-v3-turbo"


async def test_sends_base64_encoded_audio_and_correct_format():
    seen = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(
        _handler_factory(seen=seen, audio_content_type="audio/ogg")
    ))
    async with http:
        await transcribe("https://hub.spoter.com.ar/audio/1753", http=http, api_key="sk")

    openrouter_req = [r for r in seen if r.url.host == "openrouter.ai"][0]
    body = json.loads(openrouter_req.content)

    assert body["input_audio"]["data"] == base64.b64encode(AUDIO_BYTES).decode("ascii")
    assert body["input_audio"]["format"] == "ogg"
    assert body["model"] == "openai/whisper-large-v3-turbo"
    assert body["usage"] == {"include": True}


async def test_sends_bearer_authorization_header():
    seen = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(_handler_factory(seen=seen)))
    async with http:
        await transcribe("https://hub.spoter.com.ar/audio/1753", http=http, api_key="sk-or-abc")

    openrouter_req = [r for r in seen if r.url.host == "openrouter.ai"][0]
    assert openrouter_req.headers["authorization"] == "Bearer sk-or-abc"


async def test_format_derived_from_url_extension_when_content_type_missing():
    seen = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(
        _handler_factory(seen=seen, audio_content_type=None)
    ))
    async with http:
        await transcribe("https://hub.spoter.com.ar/audio/1753.m4a?token=x", http=http, api_key="sk")

    body = json.loads([r for r in seen if r.url.host == "openrouter.ai"][0].content)
    assert body["input_audio"]["format"] == "m4a"


async def test_format_defaults_when_no_hint_at_all():
    seen = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(
        _handler_factory(seen=seen, audio_content_type=None)
    ))
    async with http:
        await transcribe(
            "https://hub.spoter.com.ar/audio/1753",
            http=http,
            api_key="sk",
            default_format="ogg",
        )

    body = json.loads([r for r in seen if r.url.host == "openrouter.ai"][0].content)
    assert body["input_audio"]["format"] == "ogg"


async def test_uses_custom_model_when_provided():
    seen = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(_handler_factory(seen=seen)))
    async with http:
        await transcribe(
            "https://hub.spoter.com.ar/audio/1753",
            http=http,
            api_key="sk",
            model="openai/whisper-1",
        )

    body = json.loads([r for r in seen if r.url.host == "openrouter.ai"][0].content)
    assert body["model"] == "openai/whisper-1"


async def test_download_failure_raises_transcription_error():
    http = httpx.AsyncClient(transport=httpx.MockTransport(
        _handler_factory(audio_status=404)
    ))
    async with http:
        with pytest.raises(TranscriptionError):
            await transcribe("https://hub.spoter.com.ar/audio/1753", http=http, api_key="sk")


async def test_openrouter_error_raises_transcription_error():
    http = httpx.AsyncClient(transport=httpx.MockTransport(
        _handler_factory(openrouter_status=500, openrouter_response={"error": {"message": "internal"}})
    ))
    async with http:
        with pytest.raises(TranscriptionError):
            await transcribe("https://hub.spoter.com.ar/audio/1753", http=http, api_key="sk")


async def test_openrouter_business_error_in_200_raises():
    http = httpx.AsyncClient(transport=httpx.MockTransport(
        _handler_factory(openrouter_response={"error": {"message": "bad audio"}})
    ))
    async with http:
        with pytest.raises(TranscriptionError):
            await transcribe("https://hub.spoter.com.ar/audio/1753", http=http, api_key="sk")


async def test_missing_text_in_response_returns_empty_string():
    http = httpx.AsyncClient(transport=httpx.MockTransport(
        _handler_factory(openrouter_response={"usage": {"seconds": 1.0}})
    ))
    async with http:
        result = await transcribe("https://hub.spoter.com.ar/audio/1753", http=http, api_key="sk")

    assert result.text == ""
    assert result.duration_seconds == 1.0


async def test_content_type_wins_over_url_extension():
    seen = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(
        _handler_factory(seen=seen, audio_content_type="audio/ogg")
    ))
    async with http:
        # URL says .mp3, Content-Type says ogg. Content-Type debe ganar.
        await transcribe("https://hub.spoter.com.ar/audio/1753.mp3", http=http, api_key="sk")

    body = json.loads([r for r in seen if r.url.host == "openrouter.ai"][0].content)
    assert body["input_audio"]["format"] == "ogg"


async def test_download_uses_get_and_openrouter_uses_post():
    seen = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(_handler_factory(seen=seen)))
    async with http:
        await transcribe("https://hub.spoter.com.ar/audio/1753", http=http, api_key="sk")

    download = [r for r in seen if r.url.host != "openrouter.ai"][0]
    upload = [r for r in seen if r.url.host == "openrouter.ai"][0]
    assert download.method == "GET"
    assert upload.method == "POST"
    assert str(upload.url) == OPENROUTER_URL


async def test_transcribe_bytes_sends_bytes_and_reports_latency():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={
            "text": "hola desde bytes",
            "usage": {"seconds": 1.5, "cost": 0.0001},
        })

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with http:
        result = await transcribe_bytes(
            AUDIO_BYTES,
            http=http, api_key="sk-or-abc",
            model="openai/whisper-1",
            content_type="audio/ogg",
            filename="nota.ogg",
        )

    assert result.text == "hola desde bytes"
    assert result.duration_seconds == 1.5
    assert result.cost_usd == 0.0001
    assert result.model == "openai/whisper-1"
    assert result.latency_ms is not None
    body = json.loads(seen[0].content)
    assert body["input_audio"]["data"] == base64.b64encode(AUDIO_BYTES).decode("ascii")
    assert body["input_audio"]["format"] == "ogg"
    assert body["model"] == "openai/whisper-1"
    assert seen[0].headers["authorization"] == "Bearer sk-or-abc"


async def test_transcribe_bytes_format_from_filename_when_no_content_type():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"text": "ok", "usage": {"seconds": 1.0}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with http:
        await transcribe_bytes(
            AUDIO_BYTES, http=http, api_key="sk",
            model="openai/whisper-1", content_type=None,
            filename="nota.m4a",
        )

    body = json.loads(seen[0].content)
    assert body["input_audio"]["format"] == "m4a"
