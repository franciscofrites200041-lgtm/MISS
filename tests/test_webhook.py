from fastapi.testclient import TestClient

from app.main import app


REAL_PAYLOAD = {
    "instance": "95",
    "async": 1,
    "datos_conexion": {"mass_url": "https://hub.spoter.com.ar/api/mass.json"},
    "event_type": "transcript_audio",
    "datos_instancias": {
        "instance": "95",
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


def test_webhook_with_valid_payload_returns_202(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    client = TestClient(app)

    response = client.post(
        "/v1/spoter/webhook",
        headers={"X-Webhook-Secret": "s3cret"},
        json=REAL_PAYLOAD,
    )

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}


def test_webhook_rejects_malformed_payload_with_422(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    client = TestClient(app)

    response = client.post(
        "/v1/spoter/webhook",
        headers={"X-Webhook-Secret": "s3cret"},
        json={"event_type": "transcript_audio"},
    )

    assert response.status_code == 422


def test_webhook_with_wrong_secret_returns_401(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    client = TestClient(app)

    response = client.post(
        "/v1/spoter/webhook",
        headers={"X-Webhook-Secret": "nope"},
        json=REAL_PAYLOAD,
    )

    assert response.status_code == 401


def test_webhook_without_secret_header_returns_401(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    client = TestClient(app)

    response = client.post("/v1/spoter/webhook", json=REAL_PAYLOAD)

    assert response.status_code == 401


def test_webhook_fails_closed_when_env_not_set(monkeypatch):
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    client = TestClient(app)

    response = client.post(
        "/v1/spoter/webhook",
        headers={"X-Webhook-Secret": "anything"},
        json=REAL_PAYLOAD,
    )

    assert response.status_code == 500


def test_secret_is_verified_before_body_is_parsed(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    client = TestClient(app)

    response = client.post(
        "/v1/spoter/webhook",
        headers={"X-Webhook-Secret": "nope"},
        json={"garbage": True},
    )

    assert response.status_code == 401
