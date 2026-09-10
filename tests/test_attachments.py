from app.attachments import Attachment, extract_attachment
from app.models import SpoterWebhookPayload


def _payload(message: dict | list | None = ..., propio: int = 0, tipo: str = "audio", media_url: str | None = "https://hub.spoter.com.ar/audio/1753"):
    if message is ...:
        message = {
            "tipo": tipo,
            "propio": propio,
            "fecha_hora": "2026-09-10T08:28:32",
            "mensaje": "",
            "media_url": media_url,
        }
    return SpoterWebhookPayload.model_validate({
        "instance": "95",
        "async": 1,
        "datos_conexion": {"mass_url": "https://hub.spoter.com.ar/api/mass.json"},
        "event_type": "transcript_audio",
        "datos_instancias": {
            "instance": "95",
            "phone": "5492615617031",
            "message": message,
        },
    })


def test_extracts_audio_from_client_message():
    result = extract_attachment(_payload())

    assert result == Attachment(
        kind="audio",
        url="https://hub.spoter.com.ar/audio/1753",
        mensaje="",
    )


def test_returns_none_when_message_absent():
    result = extract_attachment(_payload(message=None))

    assert result is None


def test_returns_none_when_message_is_php_empty_array():
    result = extract_attachment(_payload(message=[]))

    assert result is None


def test_returns_none_when_audio_is_from_business():
    result = extract_attachment(_payload(propio=1))

    assert result is None


def test_returns_none_when_tipo_is_chat():
    result = extract_attachment(_payload(tipo="chat"))

    assert result is None


def test_returns_none_when_audio_has_no_media_url():
    result = extract_attachment(_payload(media_url=None))

    assert result is None


def test_extracts_image_from_client_as_stub_kind():
    result = extract_attachment(_payload(tipo="image", media_url="https://hub/img/1"))

    assert result == Attachment(kind="image", url="https://hub/img/1", mensaje="")


def test_extracts_document_from_client_as_stub_kind():
    result = extract_attachment(_payload(tipo="document", media_url="https://hub/doc/1"))

    assert result == Attachment(kind="document", url="https://hub/doc/1", mensaje="")


def test_returns_none_when_datos_instancias_is_empty():
    payload = SpoterWebhookPayload.model_validate({
        "instance": "95",
        "async": 1,
        "datos_conexion": {"mass_url": "https://hub.spoter.com.ar/api/mass.json"},
        "event_type": "transcript_audio",
        "datos_instancias": [],
    })

    assert extract_attachment(payload) is None


def test_ignores_audio_placeholder_string_in_mensaje():
    result = extract_attachment(_payload(message={
        "tipo": "audio",
        "propio": 0,
        "fecha_hora": "2026-09-10T08:28:32",
        "mensaje": "[AUDIO]",
        "media_url": "https://hub.spoter.com.ar/audio/1753",
    }))

    assert result is not None
    assert result.mensaje == ""
