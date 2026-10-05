import json

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.spoter import SpoterClient


REAL_PAYLOAD = {
    "instance": "95",
    "async": 1,
    "datos_conexion": {
        "mass_url": "https://hub.spoter.com.ar/api/mass.json",
        "token": "E2E-PAYLOAD-TOKEN-1234567890",
    },
    "event_type": "transcript_audio",
    "datos_instancias": {
        "instance": "26434",
        "phone": "5492615617031",
        "message": {
            "tipo": "audio",
            "propio": 0,
            "fecha_hora": "2026-09-10T08:28:32",
            "mensaje": "",
            "media_url": "https://hub.spoter.com.ar/audio/1753",
        },
    },
}


class FakeSpoterUniverse:
    """Router de mocks para el E2E: audio, OpenRouter, contact lookup, mass emit."""

    def __init__(self):
        self.calls = []
        self.captured_mass = None
        self.transcription_text = "hola necesito precio de lomos"

    def handler(self, request):
        self.calls.append(request)
        u = request.url
        if u.path == "/api/auth.json":
            return httpx.Response(200, json={"success": True, "token": "AUTOTOK"})
        if u.host == "openrouter.ai":
            return httpx.Response(200, json={
                "text": self.transcription_text,
                "usage": {"seconds": 4.2, "cost": 0.0005},
            })
        if u.path == "/hynts/getdata.json":
            return httpx.Response(200, json={
                "success": True,
                "data": {"nickname": "Ale Del Pozo"},
            })
        if u.path == "/api/mass.json":
            self.captured_mass = json.loads(request.content)
            return httpx.Response(200, json={"success": True, "data": []})
        if u.path.startswith("/audio/"):
            return httpx.Response(200, content=b"fake ogg", headers={"content-type": "audio/ogg"})
        return httpx.Response(404)


def _override_app_deps(fake: FakeSpoterUniverse):
    app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    app.state.spoter = SpoterClient(app.state.http)


def test_e2e_webhook_transcribes_and_emits_note(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-fake")
    monkeypatch.setenv("MISS_SERVICE_USER_ID", "1")
    monkeypatch.setenv("MISS_NOTE_PREFIX", "[Transcripción de audio]")

    fake = FakeSpoterUniverse()

    with TestClient(app) as client:
        _override_app_deps(fake)
        response = client.post(
            "/v1/spoter/webhook",
            headers={"X-Webhook-Secret": "s3cret"},
            json=REAL_PAYLOAD,
        )

    assert response.status_code == 202
    assert fake.captured_mass is not None

    body = fake.captured_mass
    assert body["instance"] == "95"
    assert body["data"] == {"instance": "26434"}
    assert body["phone"] == "5492615617031"
    assert body["origen"] == "miss"

    action = body["actions"][0]
    assert action["codigo"] == "agregar_nota"
    assert action["mensaje_nota"] == "[Cliente: Ale Del Pozo] [Transcripción de audio] hola necesito precio de lomos"
    assert action["nombre_sugerido"] == "Ale Del Pozo"
    assert action["id_user"] == "1"
    assert action["numero_sugerido"] == "5492615617031"
    assert "id_orig_quote" not in action


def test_e2e_own_media_attributes_operator_and_quote(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-fake")
    monkeypatch.setenv("MISS_SERVICE_USER_ID", "1")
    monkeypatch.setenv("MISS_NOTE_PREFIX", "[Transcripción de audio]")

    fake = FakeSpoterUniverse()
    body = {**REAL_PAYLOAD}
    body["datos_instancias"] = {
        **REAL_PAYLOAD["datos_instancias"],
        "message": {
            **REAL_PAYLOAD["datos_instancias"]["message"],
            "propio": 1,
            "operacion": "42",
            "id_original": "a1b2c3",
        },
    }

    with TestClient(app) as client:
        _override_app_deps(fake)
        response = client.post(
            "/v1/spoter/webhook",
            headers={"X-Webhook-Secret": "s3cret"},
            json=body,
        )

    assert response.status_code == 202
    assert fake.captured_mass is not None

    action = fake.captured_mass["actions"][0]
    assert action["id_user"] == "42"
    assert action["mensaje_nota"] == "[Operador: 42] [Transcripción de audio] hola necesito precio de lomos"
    assert action["id_orig_quote"] == "a1b2c3"


def test_e2e_own_media_object_operacion_attributes_operator(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-fake")
    monkeypatch.setenv("MISS_SERVICE_USER_ID", "1")
    monkeypatch.setenv("MISS_NOTE_PREFIX", "[Transcripción de audio]")

    fake = FakeSpoterUniverse()
    body = {**REAL_PAYLOAD}
    body["datos_instancias"] = {
        **REAL_PAYLOAD["datos_instancias"],
        "message": {
            **REAL_PAYLOAD["datos_instancias"]["message"],
            "propio": 1,
            "operacion": {"id_user": 10, "usa_faq": True},
            "id_original": "a1b2c3",
        },
    }

    with TestClient(app) as client:
        _override_app_deps(fake)
        response = client.post(
            "/v1/spoter/webhook",
            headers={"X-Webhook-Secret": "s3cret"},
            json=body,
        )

    assert response.status_code == 202
    assert fake.captured_mass is not None

    action = fake.captured_mass["actions"][0]
    assert action["id_user"] == "10"
    assert action["mensaje_nota"] == "[Operador: 10] [Transcripción de audio] hola necesito precio de lomos"
    assert action["id_orig_quote"] == "a1b2c3"


def test_e2e_webhook_returns_202_even_when_pipeline_short_circuits(monkeypatch):
    """Si el payload no tiene audio de cliente, el webhook igual responde 202
    (Spoter no debe recibir un error por eventos que no aplican)."""
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-fake")
    monkeypatch.setenv("MISS_SERVICE_USER_ID", "1")

    fake = FakeSpoterUniverse()

    body = {**REAL_PAYLOAD}
    body["datos_instancias"] = {
        **REAL_PAYLOAD["datos_instancias"],
        "message": {**REAL_PAYLOAD["datos_instancias"]["message"], "tipo": "chat"},
    }

    with TestClient(app) as client:
        _override_app_deps(fake)
        response = client.post(
            "/v1/spoter/webhook",
            headers={"X-Webhook-Secret": "s3cret"},
            json=body,
        )

    assert response.status_code == 202
    assert fake.captured_mass is None
