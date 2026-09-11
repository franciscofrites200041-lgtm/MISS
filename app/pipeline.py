from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.actions import SpoterMassRejected, emit_agregar_nota
from app.attachments import extract_attachment
from app.contacts import get_contact_by_phone
from app.describe import (
    DEFAULT_DESCRIPTION_MODEL,
    DescriptionError,
    describe_document,
    describe_image,
)
from app.models import SpoterWebhookPayload
from app.spoter import SpoterClient
from app.storage import RunStore
from app.transcription import DEFAULT_MODEL, TranscriptionError, transcribe

logger = logging.getLogger("miss.pipeline")


@dataclass(frozen=True)
class PipelineConfig:
    openrouter_api_key: str
    service_user_id: str
    note_prefix: str = "[Transcripción de audio]"
    note_prefix_image: str = "[Descripción de imagen]"
    note_prefix_document: str = "[Resumen de documento]"
    transcription_model: str = DEFAULT_MODEL
    description_model: str = DEFAULT_DESCRIPTION_MODEL


@dataclass(frozen=True)
class _NoteBuild:
    text: str
    prefix: str
    cost_usd: float | None = None
    duration_seconds: float | None = None


async def process_webhook(
    payload: SpoterWebhookPayload,
    *,
    http: httpx.AsyncClient,
    spoter: SpoterClient,
    config: PipelineConfig,
    store: RunStore | None = None,
) -> None:
    """Orquesta el flujo de MISS para webhooks `transcript_audio` /
    `transcript_image` / `transcript_file`: extraer adjunto -> generar texto
    (transcripción o descripción) -> resolver contacto -> emitir `agregar_nota`.
    Todas las fallas se logean; ninguna propaga (sino tumba el background task).

    Si `store` está seteado, persiste cada run con su status y resultado."""
    target = payload.datos_instancias[0] if payload.datos_instancias else None
    run_id = None
    if store is not None:
        run_id = await store.create(
            instance_root=payload.instance,
            sub_instance=target.instance if target else "",
            phone=target.phone if target else "",
            event_type=payload.event_type,
        )

    async def skip(reason: str) -> None:
        logger.info(reason)
        if store is not None and run_id is not None:
            await store.mark_skipped(run_id, reason=reason)

    async def fail(reason: str) -> None:
        logger.error(reason)
        if store is not None and run_id is not None:
            await store.mark_failed(run_id, error_message=reason)

    attachment = extract_attachment(payload)
    if attachment is None or attachment.kind not in {"audio", "image", "document"}:
        await skip("no supported attachment")
        return

    if store is not None and run_id is not None:
        await store.mark_processing(run_id)
        await store.set_attachment(run_id, kind=attachment.kind, url=attachment.url)

    if not config.openrouter_api_key:
        await skip("OPENROUTER_API_KEY not configured")
        return

    try:
        if attachment.kind == "audio":
            transcription = await transcribe(
                attachment.url,
                http=http,
                api_key=config.openrouter_api_key,
                model=config.transcription_model,
            )
            note = _NoteBuild(
                text=transcription.text.strip(),
                prefix=config.note_prefix,
                cost_usd=transcription.cost_usd,
                duration_seconds=transcription.duration_seconds,
            )
        elif attachment.kind == "image":
            description = await describe_image(
                attachment.url,
                http=http,
                api_key=config.openrouter_api_key,
                model=config.description_model,
            )
            note = _NoteBuild(
                text=description.text,
                prefix=config.note_prefix_image,
                cost_usd=description.cost_usd,
            )
        else:  # document
            description = await describe_document(
                attachment.url,
                http=http,
                api_key=config.openrouter_api_key,
                model=config.description_model,
            )
            note = _NoteBuild(
                text=description.text,
                prefix=config.note_prefix_document,
                cost_usd=description.cost_usd,
            )
    except TranscriptionError as exc:
        await fail(f"transcription failed: {exc}")
        return
    except DescriptionError as exc:
        await fail(f"description failed: {exc}")
        return

    if not note.text:
        await skip(f"{attachment.kind} produced empty text")
        return

    assert target is not None  # extract_attachment ya verificó que había target
    contact = await get_contact_by_phone(
        spoter,
        mass_url=payload.mass_url,
        instance=target.instance,
        phone=target.phone,
    )
    name = contact.name if contact else ""

    mensaje = f"{note.prefix} {note.text}".strip()

    try:
        id_original = await emit_agregar_nota(
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
        await fail(f"mass emission failed: {exc}")
        return

    if store is not None and run_id is not None:
        await store.mark_completed(
            run_id,
            transcription_text=note.text,
            transcription_cost_usd=note.cost_usd,
            transcription_duration_seconds=note.duration_seconds,
            contact_name=name or None,
            mass_id_original=id_original,
        )
