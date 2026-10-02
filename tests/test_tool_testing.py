from base64 import b64encode
import json
from unittest import mock

import httpx
from fastapi.testclient import TestClient

from app.main import app

AUTH_HEADER = {"Authorization": "Basic " + b64encode(b"admin:pw").decode()}

AUDIO_BYTES = b"\xff\xfb\x90\x00fake mp3 bytes"
IMAGE_BYTES = b"\x89PNG\r\n\x1a\nfake image bytes"
PDF_BYTES = b"%PDF-1.4 fake body"

AUDIO_RESPONSE = {"text": "hola prueba", "usage": {"seconds": 1.5, "cost": 0.0001}}
CHAT_RESPONSE = {
    "choices": [{"message": {"content": "Resultado de la prueba."}}],
    "usage": {"cost": 0.0002},
}


def _make_client(openrouter_handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(openrouter_handler))


def _openrouter(seen, status=200, chat=False):
    def handler(request):
        seen.append(request)
        if chat:
            return httpx.Response(status, json=CHAT_RESPONSE)
        return httpx.Response(status, json=AUDIO_RESPONSE)

    return handler


def _set_creds(monkeypatch):
    monkeypatch.setenv("MISS_DASHBOARD_USER", "admin")
    monkeypatch.setenv("MISS_DASHBOARD_PASS", "pw")


def test_tool_test_requires_auth(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    with TestClient(app) as client:
        response = client.post("/api/tools/audio/test")
    assert response.status_code == 401


def test_tool_test_returns_404_for_unknown_slug(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    with TestClient(app) as client:
        response = client.post("/api/tools/nope/test", headers=AUTH_HEADER)
    assert response.status_code == 404


def test_tool_test_audio_happy_path(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    seen = []
    with TestClient(app) as client:
        client.app.state.http = _make_client(_openrouter(seen))
        response = client.post(
            "/api/tools/audio/test",
            headers=AUTH_HEADER,
            files={"file": ("nota.mp3", AUDIO_BYTES, "audio/mpeg")},
            data={"model": "openai/whisper-1"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "hola prueba"
    assert body["cost_usd"] == 0.0001
    assert body["duration_seconds"] == 1.5
    assert body["latency_ms"] is not None
    assert body["model"] == "openai/whisper-1"
    assert body["file"]["name"] == "nota.mp3"
    assert body["file"]["size"] == len(AUDIO_BYTES)
    assert body["file"]["content_type"] == "audio/mpeg"
    assert seen[0].url.path == "/api/v1/audio/transcriptions"


def test_tool_test_audio_uses_configured_model_when_omitted(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    with TestClient(app) as client:
        client.app.state.http = _make_client(_openrouter([]))
        response = client.post(
            "/api/tools/audio/test",
            headers=AUTH_HEADER,
            files={"file": ("nota.mp3", AUDIO_BYTES, "audio/mpeg")},
        )
    assert response.status_code == 200
    assert response.json()["model"] == "openai/whisper-large-v3-turbo"


def test_tool_test_audio_rejects_wrong_content_type(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    with TestClient(app) as client:
        client.app.state.http = _make_client(_openrouter([]))
        response = client.post(
            "/api/tools/audio/test",
            headers=AUTH_HEADER,
            files={"file": ("nota.txt", b"hola", "text/plain")},
        )
    assert response.status_code == 400
    assert "audio" in response.json()["detail"]


def test_tool_test_image_tool_is_rejected_without_provider(monkeypatch):
    """Una DB ya existente puede conservar un tool 'image'; el endpoint debe
    rechazarlo sin invocar ningún proveedor."""
    _set_creds(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    from app.tools_store import Tool

    async def fake_get(slug):
        return Tool(
            slug="image",
            name="Descripción de imagen",
            description="desc",
            kind="image",
            enabled=True,
            model="google/gemini-2.5-flash",
            prompt="p",
            note_prefix="n",
            updated_at="2024-01-01T00:00:00+00:00",
        )

    seen = []
    with TestClient(app) as client:
        client.app.state.http = _make_client(_openrouter(seen, chat=True))
        client.app.state.tools.get = fake_get
        response = client.post(
            "/api/tools/image/test",
            headers=AUTH_HEADER,
            files={"file": ("foto.png", IMAGE_BYTES, "image/png")},
        )

    assert response.status_code == 400
    assert seen == []


def test_tool_test_image_slug_not_seeded_returns_404(monkeypatch):
    """Con seeds frescos no existe un tool 'image', así que el slug devuelve 404
    sin invocar ningún proveedor."""
    _set_creds(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    seen = []
    with TestClient(app) as client:
        client.app.state.http = _make_client(_openrouter(seen, chat=True))
        response = client.post(
            "/api/tools/image/test",
            headers=AUTH_HEADER,
            files={"file": ("foto.png", IMAGE_BYTES, "image/png")},
        )

    assert response.status_code == 404
    assert seen == []


def test_tool_test_document_textless_pdf_skips_without_provider(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    from app import describe as describe_mod
    monkeypatch.setattr(describe_mod, "_try_extract_pdf_text", lambda raw: "")

    seen = []
    with TestClient(app) as client:
        client.app.state.http = _make_client(_openrouter(seen, chat=True))
        response = client.post(
            "/api/tools/document/test",
            headers=AUTH_HEADER,
            files={"file": ("nota.pdf", PDF_BYTES, "application/pdf")},
        )

    assert response.status_code == 400
    # No debe invocarse ningún proveedor para un PDF escaneado/textless.
    assert seen == []


def test_tool_test_document_native_text_pdf_still_calls_provider(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    from app import describe as describe_mod
    monkeypatch.setattr(
        describe_mod, "_try_extract_pdf_text", lambda raw: "Texto del PDF. " * 50,
    )

    seen = []
    with TestClient(app) as client:
        client.app.state.http = _make_client(_openrouter(seen, chat=True))
        response = client.post(
            "/api/tools/document/test",
            headers=AUTH_HEADER,
            files={"file": ("nota.pdf", PDF_BYTES, "application/pdf")},
        )

    assert response.status_code == 200
    assert response.json()["text"] == "Resultado de la prueba."
    assert seen[0].url.path == "/api/v1/chat/completions"
    content = json.loads(seen[0].content)["messages"][0]["content"]
    assert all(part["type"] == "text" for part in content)


def test_tool_test_document_reports_forced_model_not_override(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    from app import describe as describe_mod
    monkeypatch.setattr(describe_mod, "_try_extract_pdf_text", lambda raw: "Texto del PDF. " * 50)

    seen = []
    with TestClient(app) as client:
        client.app.state.http = _make_client(_openrouter(seen, chat=True))
        response = client.post(
            "/api/tools/document/test",
            headers=AUTH_HEADER,
            files={"file": ("nota.pdf", PDF_BYTES, "application/pdf")},
            data={"model": "openai/gpt-4o"},
        )

    assert response.status_code == 200
    assert response.json()["model"] == "google/gemini-2.5-flash"
    body = json.loads(seen[0].content)
    assert body["model"] == "google/gemini-2.5-flash"


def test_tool_test_document_rejects_non_pdf(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    with TestClient(app) as client:
        client.app.state.http = _make_client(_openrouter([]))
        response = client.post(
            "/api/tools/document/test",
            headers=AUTH_HEADER,
            files={"file": ("nota.txt", b"hola", "text/plain")},
        )
    assert response.status_code == 400
    assert "PDF" in response.json()["detail"]


def test_tool_test_returns_502_when_openrouter_fails(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    with TestClient(app) as client:
        client.app.state.http = _make_client(_openrouter([], status=500))
        response = client.post(
            "/api/tools/audio/test",
            headers=AUTH_HEADER,
            files={"file": ("nota.mp3", AUDIO_BYTES, "audio/mpeg")},
        )
    assert response.status_code == 502


def test_tool_test_size_limit(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    with TestClient(app) as client:
        client.app.state.http = _make_client(_openrouter([]))
        with mock.patch("app.main._MAX_TEST_SIZE_BYTES", 10):
            response = client.post(
                "/api/tools/audio/test",
                headers=AUTH_HEADER,
                files={"file": ("nota.mp3", AUDIO_BYTES, "audio/mpeg")},
            )
    assert response.status_code == 400
    assert "grande" in response.json()["detail"]


def test_tool_test_returns_502_without_api_key(monkeypatch):
    _set_creds(monkeypatch)
    with TestClient(app) as client:
        client.app.state.http = _make_client(_openrouter([]))
        response = client.post(
            "/api/tools/audio/test",
            headers=AUTH_HEADER,
            files={"file": ("nota.mp3", AUDIO_BYTES, "audio/mpeg")},
        )
    assert response.status_code == 502
    assert "OPENROUTER_API_KEY" in response.json()["detail"]


def test_tool_test_missing_file_returns_400(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    with TestClient(app) as client:
        client.app.state.http = _make_client(_openrouter([]))
        response = client.post("/api/tools/audio/test", headers=AUTH_HEADER)
    assert response.status_code == 400
    assert "archivo" in response.json()["detail"]
