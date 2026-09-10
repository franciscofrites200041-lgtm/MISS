import asyncio
from base64 import b64encode

from fastapi.testclient import TestClient

from app.main import app


AUTH_HEADER = {"Authorization": "Basic " + b64encode(b"admin:pw").decode()}


def _sync(coro):
    """Corre un coroutine desde un test síncrono en un event loop nuevo. Necesario
    porque TestClient consume su propio loop interno; usar el de default entre
    tests deja loops cerrados que revientan."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _seed(store, n=3):
    async def _run():
        ids = []
        for i in range(n):
            rid = await store.create(
                instance_root="95", sub_instance="26434",
                phone=f"54926000{i}", event_type="transcript_audio",
            )
            ids.append(rid)
        return ids

    return _sync(_run())


def test_api_runs_requires_auth(monkeypatch):
    monkeypatch.setenv("MISS_DASHBOARD_USER", "admin")
    monkeypatch.setenv("MISS_DASHBOARD_PASS", "pw")

    with TestClient(app) as client:
        response = client.get("/api/runs")

    assert response.status_code == 401


def test_api_runs_rejects_wrong_credentials(monkeypatch):
    monkeypatch.setenv("MISS_DASHBOARD_USER", "admin")
    monkeypatch.setenv("MISS_DASHBOARD_PASS", "pw")

    with TestClient(app) as client:
        response = client.get(
            "/api/runs",
            headers={"Authorization": "Basic " + b64encode(b"admin:wrong").decode()},
        )

    assert response.status_code == 401


def test_api_runs_returns_list(monkeypatch):
    monkeypatch.setenv("MISS_DASHBOARD_USER", "admin")
    monkeypatch.setenv("MISS_DASHBOARD_PASS", "pw")

    with TestClient(app) as client:
        _seed(app.state.store, n=2)
        response = client.get("/api/runs", headers=AUTH_HEADER)

    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert len(body["items"]) == 2
    assert body["items"][0]["event_type"] == "transcript_audio"


def test_api_runs_pagination(monkeypatch):
    monkeypatch.setenv("MISS_DASHBOARD_USER", "admin")
    monkeypatch.setenv("MISS_DASHBOARD_PASS", "pw")

    with TestClient(app) as client:
        _seed(app.state.store, n=5)
        page1 = client.get("/api/runs?limit=2&offset=0", headers=AUTH_HEADER).json()
        page2 = client.get("/api/runs?limit=2&offset=2", headers=AUTH_HEADER).json()

    assert len(page1["items"]) == 2
    assert len(page2["items"]) == 2
    # Ids no se repiten entre páginas
    assert not (set(r["id"] for r in page1["items"]) & set(r["id"] for r in page2["items"]))


def test_api_runs_status_filter(monkeypatch):
    monkeypatch.setenv("MISS_DASHBOARD_USER", "admin")
    monkeypatch.setenv("MISS_DASHBOARD_PASS", "pw")

    import asyncio

    async def seed_with_status():
        a = await app.state.store.create(
            instance_root="95", sub_instance="26434", phone="1",
            event_type="transcript_audio",
        )
        b = await app.state.store.create(
            instance_root="95", sub_instance="26434", phone="2",
            event_type="transcript_audio",
        )
        await app.state.store.mark_completed(a, transcription_text="hola")
        return a, b

    with TestClient(app) as client:
        completed_id, _ = _sync(seed_with_status())
        response = client.get("/api/runs?status=completed", headers=AUTH_HEADER)

    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == completed_id


def test_api_run_detail(monkeypatch):
    monkeypatch.setenv("MISS_DASHBOARD_USER", "admin")
    monkeypatch.setenv("MISS_DASHBOARD_PASS", "pw")

    with TestClient(app) as client:
        ids = _seed(app.state.store, n=1)
        response = client.get(f"/api/runs/{ids[0]}", headers=AUTH_HEADER)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == ids[0]
    assert body["instance_root"] == "95"


def test_api_run_detail_returns_404_for_unknown_id(monkeypatch):
    monkeypatch.setenv("MISS_DASHBOARD_USER", "admin")
    monkeypatch.setenv("MISS_DASHBOARD_PASS", "pw")

    with TestClient(app) as client:
        response = client.get("/api/runs/nonexistent", headers=AUTH_HEADER)

    assert response.status_code == 404


def test_api_metrics_returns_summary(monkeypatch):
    monkeypatch.setenv("MISS_DASHBOARD_USER", "admin")
    monkeypatch.setenv("MISS_DASHBOARD_PASS", "pw")

    import asyncio

    async def seed():
        a = await app.state.store.create(
            instance_root="95", sub_instance="26434", phone="1",
            event_type="transcript_audio",
        )
        b = await app.state.store.create(
            instance_root="95", sub_instance="26434", phone="2",
            event_type="transcript_audio",
        )
        await app.state.store.mark_completed(a, transcription_cost_usd=0.001)
        await app.state.store.mark_failed(b, error_message="boom")

    with TestClient(app) as client:
        _sync(seed())
        response = client.get("/api/metrics", headers=AUTH_HEADER)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["completed"] == 1
    assert body["failed"] == 1
    assert body["total_cost_usd"] == 0.001


def test_api_fails_closed_when_dashboard_creds_not_set(monkeypatch):
    monkeypatch.delenv("MISS_DASHBOARD_USER", raising=False)
    monkeypatch.delenv("MISS_DASHBOARD_PASS", raising=False)

    with TestClient(app) as client:
        response = client.get("/api/runs", headers=AUTH_HEADER)

    assert response.status_code == 500


def test_health_stays_open_without_auth():
    """/health no debe pedir credenciales (lo usa NPM/monitoring)."""
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
