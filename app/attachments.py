from dataclasses import dataclass

from app.models import SpoterWebhookPayload

_SUPPORTED_KINDS = frozenset({"audio", "image", "document"})
_AUDIO_PLACEHOLDER = "[AUDIO]"


@dataclass(frozen=True)
class Attachment:
    kind: str
    url: str
    mensaje: str


def extract_attachment(payload: SpoterWebhookPayload) -> Attachment | None:
    if not payload.datos_instancias:
        return None

    message = payload.datos_instancias[0].message
    if message is None:
        return None
    if message.tipo not in _SUPPORTED_KINDS:
        return None
    if not message.media_url:
        return None

    mensaje = message.mensaje or ""
    if message.tipo == "audio" and mensaje == _AUDIO_PLACEHOLDER:
        mensaje = ""

    return Attachment(kind=message.tipo, url=message.media_url, mensaje=mensaje)
