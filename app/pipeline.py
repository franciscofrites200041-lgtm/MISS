from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.actions import SpoterMassRejected, emit_agregar_nota
from app.attachments import extract_attachment
from app.contacts import get_contact_by_phone
from app.models import SpoterWebhookPayload
from app.spoter import SpoterClient
from app.transcription import DEFAULT_MODEL, TranscriptionError, transcribe

logger = logging.getLogger("miss.pipeline")


@dataclass(frozen=True)
class PipelineConfig:
    openrouter_api_key: str
    service_user_id: str
    note_prefix: str = "[Transcripción de audio]"
    transcription_model: str = DEFAULT_MODEL


async def process_webhook(
    payload: SpoterWebhookPayload,
    *,
    http: httpx.AsyncClient,
    spoter: SpoterClient,
    config: PipelineConfig,
) -> None:
    """Orquesta el flujo completo de MISS para un webhook `transcript_audio`:
    extraer audio → transcribir → resolver contacto → emitir `agregar_nota`.
    Todas las fallas se logean; ninguna propaga (sino tumba el background task)."""
    attachment = extract_attachment(payload)
    if attachment is None or attachment.kind != "audio":
        logger.info("no client audio attachment; skipping")
        return

    if not config.openrouter_api_key:
        logger.warning("OPENROUTER_API_KEY not configured; skipping transcription")
        return

    try:
        transcription = await transcribe(
            attachment.url,
            http=http,
            api_key=config.openrouter_api_key,
            model=config.transcription_model,
        )
    except TranscriptionError as exc:
        logger.error("transcription failed: %s", exc)
        return

    text = transcription.text.strip()
    if not text:
        logger.warning("transcription returned empty text; skipping emit")
        return

    target = payload.datos_instancias[0]
    contact = await get_contact_by_phone(
        spoter,
        mass_url=payload.mass_url,
        instance=target.instance,
        phone=target.phone,
    )
    name = contact.name if contact else ""

    mensaje = f"{config.note_prefix} {text}".strip()

    try:
        await emit_agregar_nota(
            spoter,
            mass_url=payload.mass_url,
            root_instance=payload.instance,
            sub_instance=target.instance,
            phone=target.phone,
            mensaje_nota=mensaje,
            id_user=config.service_user_id,
            nombre_sugerido=name,
        )
    except SpoterMassRejected as exc:
        logger.error("mass emission failed: %s", exc)
