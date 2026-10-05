import base64
import json

import httpx

from app.models import SpoterWebhookPayload
from app.pipeline import PipelineConfig, _note_header, process_webhook
from app.spoter import SpoterClient


REAL_PAYLOAD = {
    "instance": "95",
    "async": 1,
    "datos_conexion": {
        "mass_url": "https://hub.spoter.com.ar/api/mass.json",
        "token": "TEST-PAYLOAD-TOKEN-1234567890",
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
        self.operators_response = {"success": True, "data": []}
        self.operators_status = 200
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
            if u.params.get("entity") == "operadores":
                return httpx.Response(self.operators_status, json=self.operators_response)
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
    spoter = SpoterClient(http)
    return http, spoter


def _config(**overrides):
    defaults = dict(
        openrouter_api_key="sk-or-fake",
        service_user_id="1",
    )
    defaults.update(overrides)
    return PipelineConfig(**defaults)


def test_note_header_is_bracketed_for_inbound_and_own_media():
    assert _note_header(False, "document", None, "Técnica Nely") == "[Documento recibido de Técnica Nely]"
    assert _note_header(True, "audio", "42", "", "María Pérez") == "[Audio enviado por María Pérez]"


async def test_happy_path_hits_every_stage_and_emits_correct_note(tools):
    fake = FakeSpoter()
    http, spoter = _wire(fake)
    payload = SpoterWebhookPayload.model_validate(REAL_PAYLOAD)

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config(), tools=tools)

    hit_paths = {(r.url.host, r.url.path) for r in fake.calls}
    assert ("openrouter.ai", "/api/v1/audio/transcriptions") in hit_paths
    assert ("hub.spoter.com.ar", "/hynts/getdata.json") in hit_paths
    assert ("hub.spoter.com.ar", "/api/mass.json") in hit_paths
    assert ("hub.spoter.com.ar", "/audio/1753") in hit_paths

    assert fake.captured_mass_body is not None
    action = fake.captured_mass_body["actions"][0]
    assert action["codigo"] == "agregar_nota"
    assert action["mensaje_nota"] == "[Audio recibido de Ale Del Pozo]\n\nhola necesito precio de lomos"
    assert action["numero_sugerido"] == "5492615617031"
    assert action["nombre_sugerido"] == "Ale Del Pozo"
    assert action["id_user"] == "1"
    assert "id_orig_quote" not in action
    assert fake.captured_mass_body["instance"] == "95"
    assert fake.captured_mass_body["data"] == {"instance": "26434"}


async def test_own_media_uses_operacion_as_id_user_and_identifies_operator(tools):
    fake = FakeSpoter()
    http, spoter = _wire(fake)
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
    payload = SpoterWebhookPayload.model_validate(body)

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config(), tools=tools)

    action = fake.captured_mass_body["actions"][0]
    assert action["id_user"] == "42"
    assert action["mensaje_nota"] == "[Audio enviado por Operador 42]\n\nhola necesito precio de lomos"
    assert action["id_orig_quote"] == "a1b2c3"


async def test_own_media_uses_cached_operator_name_in_header(tools):
    fake = FakeSpoter()
    fake.operators_response = {"success": True, "data": [{"id": "42", "nombre": "María Pérez"}]}
    http, spoter = _wire(fake)
    body = {**REAL_PAYLOAD}
    body["datos_instancias"] = {
        **REAL_PAYLOAD["datos_instancias"],
        "message": {
            **REAL_PAYLOAD["datos_instancias"]["message"],
            "propio": 1,
            "operacion": "42",
        },
    }

    async with http:
        await process_webhook(
            SpoterWebhookPayload.model_validate(body),
            http=http,
            spoter=spoter,
            config=_config(),
            tools=tools,
        )

    action = fake.captured_mass_body["actions"][0]
    assert action["mensaje_nota"] == "[Audio enviado por María Pérez]\n\nhola necesito precio de lomos"
    operator_requests = [r for r in fake.calls if r.url.params.get("entity") == "operadores"]
    assert len(operator_requests) == 1


async def test_own_media_uses_object_operacion_id_user(tools):
    fake = FakeSpoter()
    http, spoter = _wire(fake)
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
    payload = SpoterWebhookPayload.model_validate(body)

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config(), tools=tools)

    action = fake.captured_mass_body["actions"][0]
    assert action["id_user"] == "10"
    assert action["mensaje_nota"] == "[Audio enviado por Operador 10]\n\nhola necesito precio de lomos"
    assert action["id_orig_quote"] == "a1b2c3"


async def test_own_media_with_object_operacion_missing_id_user_falls_back(tools):
    fake = FakeSpoter()
    http, spoter = _wire(fake)
    body = {**REAL_PAYLOAD}
    body["datos_instancias"] = {
        **REAL_PAYLOAD["datos_instancias"],
        "message": {
            **REAL_PAYLOAD["datos_instancias"]["message"],
            "propio": 1,
            "operacion": {"usa_faq": True},
            "id_original": "a1b2c3",
        },
    }
    payload = SpoterWebhookPayload.model_validate(body)

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config(), tools=tools)

    action = fake.captured_mass_body["actions"][0]
    assert action["id_user"] == "1"
    assert action["mensaje_nota"] == "[Audio enviado por Operador]\n\nhola necesito precio de lomos"
    assert action["id_orig_quote"] == "a1b2c3"


async def test_own_media_without_operacion_retains_service_user_and_falls_back(tools):
    fake = FakeSpoter()
    http, spoter = _wire(fake)
    body = {**REAL_PAYLOAD}
    body["datos_instancias"] = {
        **REAL_PAYLOAD["datos_instancias"],
        "message": {
            **REAL_PAYLOAD["datos_instancias"]["message"],
            "propio": 1,
        },
    }
    payload = SpoterWebhookPayload.model_validate(body)

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config(), tools=tools)

    action = fake.captured_mass_body["actions"][0]
    assert action["id_user"] == "1"
    assert action["mensaje_nota"] == "[Audio enviado por Operador]\n\nhola necesito precio de lomos"


async def test_inbound_ignores_operacion_for_id_user(tools):
    fake = FakeSpoter()
    http, spoter = _wire(fake)
    body = {**REAL_PAYLOAD}
    body["datos_instancias"] = {
        **REAL_PAYLOAD["datos_instancias"],
        "message": {
            **REAL_PAYLOAD["datos_instancias"]["message"],
            "propio": 0,
            "operacion": "42",
        },
    }
    payload = SpoterWebhookPayload.model_validate(body)

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config(), tools=tools)

    action = fake.captured_mass_body["actions"][0]
    assert action["id_user"] == "1"
    assert action["mensaje_nota"] == "[Audio recibido de Ale Del Pozo]\n\nhola necesito precio de lomos"


async def test_inbound_ignores_object_operacion_id_user(tools):
    fake = FakeSpoter()
    http, spoter = _wire(fake)
    body = {**REAL_PAYLOAD}
    body["datos_instancias"] = {
        **REAL_PAYLOAD["datos_instancias"],
        "message": {
            **REAL_PAYLOAD["datos_instancias"]["message"],
            "propio": 0,
            "operacion": {"id_user": 10, "usa_faq": True},
        },
    }
    payload = SpoterWebhookPayload.model_validate(body)

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config(), tools=tools)

    action = fake.captured_mass_body["actions"][0]
    assert action["id_user"] == "1"
    assert action["mensaje_nota"] == "[Audio recibido de Ale Del Pozo]\n\nhola necesito precio de lomos"


async def test_emits_with_empty_name_when_contact_not_found(tools):
    fake = FakeSpoter()
    fake.contact_response = {"success": True, "data": {}}
    http, spoter = _wire(fake)

    payload = SpoterWebhookPayload.model_validate(REAL_PAYLOAD)
    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config(), tools=tools)

    action = fake.captured_mass_body["actions"][0]
    assert "nombre_sugerido" not in action
    assert action["id_user"] == "1"
    assert action["mensaje_nota"] == "[Audio recibido del cliente]\n\nhola necesito precio de lomos"


async def test_skips_when_no_audio_attachment(tools):
    fake = FakeSpoter()
    http, spoter = _wire(fake)

    body = {**REAL_PAYLOAD}
    body["datos_instancias"] = {
        **REAL_PAYLOAD["datos_instancias"],
        "message": {**REAL_PAYLOAD["datos_instancias"]["message"], "tipo": "chat"},
    }
    payload = SpoterWebhookPayload.model_validate(body)

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config(), tools=tools)

    hit_hosts = {r.url.host for r in fake.calls}
    assert "openrouter.ai" not in hit_hosts
    assert fake.captured_mass_body is None


async def test_skips_transcription_when_api_key_missing(tools):
    fake = FakeSpoter()
    http, spoter = _wire(fake)
    payload = SpoterWebhookPayload.model_validate(REAL_PAYLOAD)

    async with http:
        await process_webhook(
            payload, http=http, spoter=spoter,
            config=_config(openrouter_api_key=""), tools=tools,
        )

    hit_hosts = {r.url.host for r in fake.calls}
    assert "openrouter.ai" not in hit_hosts
    assert fake.captured_mass_body is None


async def test_skips_emit_when_transcription_returns_empty_text(tools):
    fake = FakeSpoter()
    fake.openrouter_response = {"text": "  ", "usage": {"seconds": 0.1}}
    http, spoter = _wire(fake)
    payload = SpoterWebhookPayload.model_validate(REAL_PAYLOAD)

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config(), tools=tools)

    assert fake.captured_mass_body is None


async def test_swallows_transcription_error_without_emitting(tools):
    fake = FakeSpoter()
    fake.openrouter_status = 500
    fake.openrouter_response = {"error": {"message": "boom"}}
    http, spoter = _wire(fake)
    payload = SpoterWebhookPayload.model_validate(REAL_PAYLOAD)

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config(), tools=tools)

    assert fake.captured_mass_body is None


async def test_swallows_mass_emission_error(tools):
    # Emit falla → NO propaga (para no tumbar el background task).
    fake = FakeSpoter()
    fake.mass_response = {"success": False, "error": "bad"}
    http, spoter = _wire(fake)
    payload = SpoterWebhookPayload.model_validate(REAL_PAYLOAD)

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config(), tools=tools)

    assert fake.captured_mass_body is not None  # se intentó


async def test_persists_completed_run_with_all_data(tmp_path, tools):
    from app.storage import RunStore

    fake = FakeSpoter()
    http, spoter = _wire(fake)
    store = RunStore(tmp_path / "runs.db")
    await store.init()

    payload = SpoterWebhookPayload.model_validate(REAL_PAYLOAD)
    async with http:
        await process_webhook(
            payload, http=http, spoter=spoter, config=_config(), tools=tools, store=store,
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


async def test_persists_skipped_run_when_no_audio(tmp_path, tools):
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
            payload, http=http, spoter=spoter, config=_config(), tools=tools, store=store,
        )

    rows = await store.list()
    assert len(rows) == 1
    assert rows[0].status == "skipped"
    assert "no supported attachment" in (rows[0].error_message or "")


async def test_persists_failed_run_when_mass_emission_fails(tmp_path, tools):
    from app.storage import RunStore

    fake = FakeSpoter()
    fake.mass_response = {"success": False, "error": "bad"}
    http, spoter = _wire(fake)
    store = RunStore(tmp_path / "runs.db")
    await store.init()

    payload = SpoterWebhookPayload.model_validate(REAL_PAYLOAD)
    async with http:
        await process_webhook(
            payload, http=http, spoter=spoter, config=_config(), tools=tools, store=store,
        )

    rows = await store.list()
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert "mass emission" in (rows[0].error_message or "")


class FakeSpoterMulti(FakeSpoter):
    """Extiende FakeSpoter para servir imágenes/PDFs y respuestas de chat."""

    def __init__(self):
        super().__init__()
        self.image_bytes = b"\x89PNG\r\n\x1a\nfake"
        self.image_content_type = "image/png"
        self.pdf_bytes = b"%PDF-1.4 fake body"
        self.pdf_content_type = "application/pdf"
        self.chat_response = {
            "choices": [{"message": {"content": "Presupuesto por $50.000 firmado el 10/09/2026."}}],
            "usage": {"cost": 0.0002},
        }

    def handler(self, request):
        self.calls.append(request)
        u = request.url
        if u.host == "openrouter.ai" and u.path == "/api/v1/chat/completions":
            return httpx.Response(200, json=self.chat_response)
        if u.path.startswith("/image/"):
            return httpx.Response(
                200, content=self.image_bytes,
                headers={"content-type": self.image_content_type},
            )
        if u.path.startswith("/file/"):
            return httpx.Response(
                200, content=self.pdf_bytes,
                headers={"content-type": self.pdf_content_type},
            )
        return super().handler(request)


def _payload_with(message):
    body = {**REAL_PAYLOAD}
    body["datos_instancias"] = {
        **REAL_PAYLOAD["datos_instancias"], "message": message,
    }
    return SpoterWebhookPayload.model_validate(body)


async def test_image_event_is_skipped_without_any_llm_call(tools):
    fake = FakeSpoterMulti()
    http, spoter = _wire(fake)
    payload = _payload_with({
        "tipo": "image", "propio": 0, "fecha_hora": "2026-09-10T08:28:32",
        "mensaje": "", "media_url": "https://hub.spoter.com.ar/image/42",
    })

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config(), tools=tools)

    hit_hosts = {r.url.host for r in fake.calls}
    assert "openrouter.ai" not in hit_hosts
    assert fake.captured_mass_body is None


async def test_document_pdf_with_extractable_text_skips_vision(monkeypatch, tools):
    # Forzamos que pypdf devuelva texto suficiente → NO se envía el PDF binario.
    from app import describe as describe_mod
    monkeypatch.setattr(
        describe_mod, "_try_extract_pdf_text",
        lambda raw: "Presupuesto N° 123 por $50.000 emitido el 10/09/2026. " * 20,
    )

    fake = FakeSpoterMulti()
    http, spoter = _wire(fake)
    payload = _payload_with({
        "tipo": "document", "propio": 0, "fecha_hora": "2026-09-10T08:28:32",
        "mensaje": "", "media_url": "https://hub.spoter.com.ar/file/99.pdf",
    })

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config(), tools=tools)

    chat_reqs = [r for r in fake.calls if r.url.path == "/api/v1/chat/completions"]
    assert len(chat_reqs) == 1
    body = json.loads(chat_reqs[0].content)
    content = body["messages"][0]["content"]
    # Con texto extraído, el content debe ser solo texto (no `file`).
    assert all(part["type"] == "text" for part in content)

    action = fake.captured_mass_body["actions"][0]
    assert action["mensaje_nota"] == "[Documento recibido de Ale Del Pozo]\n\nPresupuesto por $50.000 firmado el 10/09/2026."


async def test_own_document_renders_natural_operator_header(monkeypatch, tools):
    from app import describe as describe_mod
    monkeypatch.setattr(describe_mod, "_try_extract_pdf_text", lambda raw: "Presupuesto N° 123 por $50.000 emitido el 10/09/2026. " * 20)

    fake = FakeSpoterMulti()
    http, spoter = _wire(fake)
    payload = _payload_with({
        "tipo": "document", "propio": 1, "fecha_hora": "2026-09-10T08:28:32",
        "mensaje": "", "media_url": "https://hub.spoter.com.ar/file/99.pdf",
        "operacion": "42",
        "id_original": "a1b2c3",
    })

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config(), tools=tools)

    action = fake.captured_mass_body["actions"][0]
    assert action["id_user"] == "42"
    assert action["mensaje_nota"] == "[Documento enviado por Operador 42]\n\nPresupuesto por $50.000 firmado el 10/09/2026."
    assert action["id_orig_quote"] == "a1b2c3"


async def test_inbound_document_without_contact_uses_generic_header(monkeypatch, tools):
    from app import describe as describe_mod
    monkeypatch.setattr(describe_mod, "_try_extract_pdf_text", lambda raw: "Presupuesto N° 123 por $50.000 emitido el 10/09/2026. " * 20)

    fake = FakeSpoterMulti()
    fake.contact_response = {"success": True, "data": {}}
    http, spoter = _wire(fake)
    payload = _payload_with({
        "tipo": "document", "propio": 0, "fecha_hora": "2026-09-10T08:28:32",
        "mensaje": "", "media_url": "https://hub.spoter.com.ar/file/99.pdf",
    })

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config(), tools=tools)

    action = fake.captured_mass_body["actions"][0]
    assert action["mensaje_nota"] == "[Documento recibido del cliente]\n\nPresupuesto por $50.000 firmado el 10/09/2026."


async def test_document_pdf_forces_flash_model_even_if_tool_overridden(monkeypatch, tools):
    from app import describe as describe_mod
    monkeypatch.setattr(describe_mod, "_try_extract_pdf_text", lambda raw: "Presupuesto N° 123. " * 20)
    await tools.update("document", model="openai/gpt-4o")

    fake = FakeSpoterMulti()
    http, spoter = _wire(fake)
    payload = _payload_with({
        "tipo": "document", "propio": 0, "fecha_hora": "2026-09-10T08:28:32",
        "mensaje": "", "media_url": "https://hub.spoter.com.ar/file/99.pdf",
    })

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config(), tools=tools)

    chat_reqs = [r for r in fake.calls if r.url.path == "/api/v1/chat/completions"]
    assert len(chat_reqs) == 1
    body = json.loads(chat_reqs[0].content)
    assert body["model"] == "google/gemini-2.5-flash"


async def test_document_pdf_without_text_is_skipped(monkeypatch, tools):
    from app import describe as describe_mod
    monkeypatch.setattr(describe_mod, "_try_extract_pdf_text", lambda raw: "")

    fake = FakeSpoterMulti()
    http, spoter = _wire(fake)
    payload = _payload_with({
        "tipo": "document", "propio": 0, "fecha_hora": "2026-09-10T08:28:32",
        "mensaje": "", "media_url": "https://hub.spoter.com.ar/file/99.pdf",
    })

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config(), tools=tools)

    hit_hosts = {r.url.host for r in fake.calls}
    assert "openrouter.ai" not in hit_hosts
    assert fake.captured_mass_body is None


async def test_document_non_pdf_is_skipped(tmp_path, tools):
    from app.storage import RunStore

    fake = FakeSpoterMulti()
    fake.pdf_content_type = "text/plain"
    fake.pdf_bytes = b"plain text"
    http, spoter = _wire(fake)
    store = RunStore(tmp_path / "runs.db")
    await store.init()
    payload = _payload_with({
        "tipo": "document", "propio": 0, "fecha_hora": "2026-09-10T08:28:32",
        "mensaje": "", "media_url": "https://hub.spoter.com.ar/file/nota.txt",
    })

    async with http:
        await process_webhook(
            payload, http=http, spoter=spoter, config=_config(), tools=tools, store=store,
        )

    hit_hosts = {r.url.host for r in fake.calls}
    assert "openrouter.ai" not in hit_hosts
    assert fake.captured_mass_body is None

    rows = await store.list()
    assert len(rows) == 1
    assert rows[0].status == "skipped"


async def test_token_from_payload_skips_login(tools):
    fake = FakeSpoter()
    http, spoter = _wire(fake)

    body = {**REAL_PAYLOAD}
    body["datos_conexion"] = {
        "mass_url": REAL_PAYLOAD["datos_conexion"]["mass_url"],
        "token": "TOK-FROM-PAYLOAD-1234567890",
    }
    payload = SpoterWebhookPayload.model_validate(body)

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config(), tools=tools)

    # Emitió la nota (había token) pero NO llamó a /api/auth.json (bypass del login).
    hit_paths = {(r.url.host, r.url.path) for r in fake.calls}
    assert ("hub.spoter.com.ar", "/api/mass.json") in hit_paths
    assert ("hub.spoter.com.ar", "/api/auth.json") not in hit_paths

    # El header X-Csrf-Spoter debe llevar el token del payload.
    mass_req = [r for r in fake.calls if r.url.path == "/api/mass.json"][0]
    assert mass_req.headers.get("x-csrf-spoter") == "TOK-FROM-PAYLOAD-1234567890"


async def test_audio_bytes_flow_end_to_end(tools):
    fake = FakeSpoter()
    fake.audio_bytes = b"custom audio bytes"
    http, spoter = _wire(fake)
    payload = SpoterWebhookPayload.model_validate(REAL_PAYLOAD)

    async with http:
        await process_webhook(payload, http=http, spoter=spoter, config=_config(), tools=tools)

    openrouter_req = [r for r in fake.calls if r.url.host == "openrouter.ai"][0]
    body = json.loads(openrouter_req.content)
    assert body["input_audio"]["data"] == base64.b64encode(b"custom audio bytes").decode("ascii")
    assert body["input_audio"]["format"] == "ogg"  # from content-type
