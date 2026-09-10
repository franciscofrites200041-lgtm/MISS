import base64
import json

import httpx

from app.models import SpoterWebhookPayload
from app.pipeline import PipelineConfig, process_webhook
from app.spoter import SpoterClient, TokenCache


REAL_PAYLOAD = {
    "instance": "95",
    "async": 1,
    "datos_conexion": {"mass_url": "https://hub.spoter.com.ar/api/mass.json"},
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


class FakeSpoter:
    """Router de mocks. Cada test declara handlers para las rutas que le importan;
    lo que quede afuera devuelve 404 y se registra."""

    def __init__(self):
        self.calls = []
        self.audio_bytes = b"\xff\xfb\x90\x00fake mp3"
        self.audio_content_type = "audio/ogg"
        self.openrouter_response = {
            "text": "hola necesito precio de lomos",
            "usage": {"seconds": 4.2, "cost": 0.0005},
        }
        self.openrouter_status = 200
        self.contact_response = {"success": True, "data": {"nickname": "Ale Del Pozo"}}
        self.contact_status = 200
        self.mass_response = {"success": True, "data": []}
        self.mass_status = 200
        self.captured_mass_body = None

    def handler(self, request):
        self.calls.append(request)
        u = request.url
        if u.path == "/api/auth.json":
            return httpx.Response(200, json={"token": "TOK"})
        if u.host == "openrouter.ai":
            return httpx.Response(self.openrouter_status, json=self.openrouter_response)
        if u.path == "/hynts/getdata.json":
            return httpx.Response(self.contact_status, json=self.contact_response)
        if u.path == "/api/mass.json":
            self.captured_mass_body = json.loads(request.content)
            return httpx.Response(self.mass_status, json=self.mass_response)
        if u.path.startswith("/audio/"):
            return httpx.Response(200, content=self.audio_bytes,
                                  headers={"content-type": self.audio_content_type})
        return httpx.Response(404)


def _wire(fake):
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    spoter = SpoterClient(http, email="e@x", password="pw", cache=TokenCache())
    return http, spoter


def _config(**overrides):
    defaults = dict(
        openrouter_api_key="sk-or-fake",
        service_user_id="1",
        note_prefix="[Transcripción de audio]",
    )
    defaults.update(overrides)
    return PipelineConfig(**defaults)


async def test_happy_path_hits_every_stage_and_emits_correct_note():
    fake = FakeSpoter()
    http, spoter = _wire(fake)
    payload = SpoterWebhookPayload.model_validate(REAL_PAYLOAD)

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config())

    hit_paths = {(r.url.host, r.url.path) for r in fake.calls}
    assert ("openrouter.ai", "/api/v1/audio/transcriptions") in hit_paths
    assert ("hub.spoter.com.ar", "/hynts/getdata.json") in hit_paths
    assert ("hub.spoter.com.ar", "/api/mass.json") in hit_paths
    assert ("hub.spoter.com.ar", "/audio/1753") in hit_paths

    assert fake.captured_mass_body is not None
    action = fake.captured_mass_body["actions"][0]
    assert action["codigo"] == "agregar_nota"
    assert action["mensaje_nota"] == "[Transcripción de audio] hola necesito precio de lomos"
    assert action["numero_sugerido"] == "5492615617031"
    assert action["nombre_sugerido"] == "Ale Del Pozo"
    assert action["id_user"] == "1"
    assert fake.captured_mass_body["instance"] == "95"
    assert fake.captured_mass_body["data"] == {"instance": "26434"}


async def test_emits_with_empty_name_when_contact_not_found():
    fake = FakeSpoter()
    fake.contact_response = {"success": True, "data": {}}
    http, spoter = _wire(fake)

    payload = SpoterWebhookPayload.model_validate(REAL_PAYLOAD)
    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config())

    action = fake.captured_mass_body["actions"][0]
    assert "nombre_sugerido" not in action


async def test_skips_when_no_audio_attachment():
    fake = FakeSpoter()
    http, spoter = _wire(fake)

    body = {**REAL_PAYLOAD}
    body["datos_instancias"] = {
        **REAL_PAYLOAD["datos_instancias"],
        "message": {**REAL_PAYLOAD["datos_instancias"]["message"], "tipo": "chat"},
    }
    payload = SpoterWebhookPayload.model_validate(body)

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config())

    hit_hosts = {r.url.host for r in fake.calls}
    assert "openrouter.ai" not in hit_hosts
    assert fake.captured_mass_body is None


async def test_skips_when_audio_is_from_business():
    fake = FakeSpoter()
    http, spoter = _wire(fake)

    body = {**REAL_PAYLOAD}
    body["datos_instancias"] = {
        **REAL_PAYLOAD["datos_instancias"],
        "message": {**REAL_PAYLOAD["datos_instancias"]["message"], "propio": 1},
    }
    payload = SpoterWebhookPayload.model_validate(body)

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config())

    assert fake.captured_mass_body is None


async def test_skips_transcription_when_api_key_missing():
    fake = FakeSpoter()
    http, spoter = _wire(fake)
    payload = SpoterWebhookPayload.model_validate(REAL_PAYLOAD)

    async with http:
        await process_webhook(
            payload, http=http, spoter=spoter,
            config=_config(openrouter_api_key=""),
        )

    hit_hosts = {r.url.host for r in fake.calls}
    assert "openrouter.ai" not in hit_hosts
    assert fake.captured_mass_body is None


async def test_skips_emit_when_transcription_returns_empty_text():
    fake = FakeSpoter()
    fake.openrouter_response = {"text": "  ", "usage": {"seconds": 0.1}}
    http, spoter = _wire(fake)
    payload = SpoterWebhookPayload.model_validate(REAL_PAYLOAD)

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config())

    assert fake.captured_mass_body is None


async def test_swallows_transcription_error_without_emitting():
    fake = FakeSpoter()
    fake.openrouter_status = 500
    fake.openrouter_response = {"error": {"message": "boom"}}
    http, spoter = _wire(fake)
    payload = SpoterWebhookPayload.model_validate(REAL_PAYLOAD)

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config())

    assert fake.captured_mass_body is None


async def test_swallows_mass_emission_error():
    # Emit falla → NO propaga (para no tumbar el background task).
    fake = FakeSpoter()
    fake.mass_response = {"success": False, "error": "bad"}
    http, spoter = _wire(fake)
    payload = SpoterWebhookPayload.model_validate(REAL_PAYLOAD)

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config())

    assert fake.captured_mass_body is not None  # se intentó


async def test_persists_completed_run_with_all_data(tmp_path):
    from app.storage import RunStore

    fake = FakeSpoter()
    http, spoter = _wire(fake)
    store = RunStore(tmp_path / "runs.db")
    await store.init()

    payload = SpoterWebhookPayload.model_validate(REAL_PAYLOAD)
    async with http:
        await process_webhook(
            payload, http=http, spoter=spoter, config=_config(), store=store,
        )

    rows = await store.list()
    assert len(rows) == 1
    run = rows[0]
    assert run.status == "completed"
    assert run.instance_root == "95"
    assert run.sub_instance == "26434"
    assert run.phone == "5492615617031"
    assert run.event_type == "transcript_audio"
    assert run.attachment_kind == "audio"
    assert run.attachment_url == "https://hub.spoter.com.ar/audio/1753"
    assert run.transcription_text == "hola necesito precio de lomos"
    assert run.transcription_cost_usd == 0.0005
    assert run.transcription_duration_seconds == 4.2
    assert run.contact_name == "Ale Del Pozo"
    assert run.mass_id_original is not None


async def test_persists_skipped_run_when_no_audio(tmp_path):
    from app.storage import RunStore

    fake = FakeSpoter()
    http, spoter = _wire(fake)
    store = RunStore(tmp_path / "runs.db")
    await store.init()

    body = {**REAL_PAYLOAD}
    body["datos_instancias"] = {
        **REAL_PAYLOAD["datos_instancias"],
        "message": {**REAL_PAYLOAD["datos_instancias"]["message"], "tipo": "chat"},
    }
    payload = SpoterWebhookPayload.model_validate(body)

    async with http:
        await process_webhook(
            payload, http=http, spoter=spoter, config=_config(), store=store,
        )

    rows = await store.list()
    assert len(rows) == 1
    assert rows[0].status == "skipped"
    assert "no client audio" in (rows[0].error_message or "")


async def test_persists_failed_run_when_mass_emission_fails(tmp_path):
    from app.storage import RunStore

    fake = FakeSpoter()
    fake.mass_response = {"success": False, "error": "bad"}
    http, spoter = _wire(fake)
    store = RunStore(tmp_path / "runs.db")
    await store.init()

    payload = SpoterWebhookPayload.model_validate(REAL_PAYLOAD)
    async with http:
        await process_webhook(
            payload, http=http, spoter=spoter, config=_config(), store=store,
        )

    rows = await store.list()
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert "mass emission" in (rows[0].error_message or "")


async def test_audio_bytes_flow_end_to_end():
    fake = FakeSpoter()
    fake.audio_bytes = b"custom audio bytes"
    http, spoter = _wire(fake)
    payload = SpoterWebhookPayload.model_validate(REAL_PAYLOAD)

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config())

    openrouter_req = [r for r in fake.calls if r.url.host == "openrouter.ai"][0]
    body = json.loads(openrouter_req.content)
    assert body["input_audio"]["data"] == base64.b64encode(b"custom audio bytes").decode("ascii")
    assert body["input_audio"]["format"] == "ogg"  # from content-type
