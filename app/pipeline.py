from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.actions import SpoterMassRejected, emit_agregar_nota
from app.attachments import extract_attachment
from app.contacts import get_contact_by_phone
from app.describe import DescriptionError, describe_document, describe_image
from app.models import SpoterWebhookPayload
from app.spoter import SpoterAuthError, SpoterClient
from app.storage import RunStore
from app.tools_store import Tool, ToolsStore
from app.transcription import TranscriptionError, transcribe

logger = logging.getLogger("miss.pipeline")


@dataclass(frozen=True)
class PipelineConfig:
    """Config mínima que no vive por tool: credenciales globales + identidad."""
    openrouter_api_key: str
    service_user_id: str


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
    tools: ToolsStore,
    store: RunStore | None = None,
) -> None:
    """Orquesta el flujo de MISS: extraer adjunto -> dispatch a la tool por
    attachment.kind -> generar texto -> resolver contacto -> emitir agregar_nota.
    La config de cada tool (modelo, prompt, prefijo, enabled) viene de ToolsStore.
    Todas las fallas se logean; ninguna propaga."""
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

    tool = await tools.get_by_kind(attachment.kind)
    if tool is None:
        await skip(f"no tool registered for kind={attachment.kind!r}")
        return
    if not tool.enabled:
        await skip(f"tool {tool.slug!r} is disabled")
        return

    if store is not None and run_id is not None:
        await store.mark_processing(run_id)
        await store.set_attachment(run_id, kind=attachment.kind, url=attachment.url)

    if not config.openrouter_api_key:
        await skip("OPENROUTER_API_KEY not configured")
        return

    try:
        note = await _run_tool(
            tool, attachment.url, http=http, api_key=config.openrouter_api_key,
        )
    except TranscriptionError as exc:
        await fail(f"transcription failed: {exc}")
        return
    except DescriptionError as exc:
        await fail(f"description failed: {exc}")
        return

    if not note.text:
        await skip(f"{tool.slug} produced empty text")
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
    except SpoterAuthError as exc:
        await fail(f"spoter auth failed: {exc}")
        return
    except SpoterMassRejected as exc:
        await fail(f"mass emission failed: {exc}")
        return
    except Exception as exc:
        # Última red: cualquier cosa que no anticipamos no debe tumbar el
        # background task ni dejar la run en 'processing'.
        logger.exception("unexpected error emitting note")
        await fail(f"unexpected: {exc.__class__.__name__}: {exc}")
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


async def _run_tool(
    tool: Tool,
    url: str,
    *,
    http: httpx.AsyncClient,
    api_key: str,
) -> _NoteBuild:
    if tool.kind == "audio":
        transcription = await transcribe(
            url, http=http, api_key=api_key, model=tool.model,
        )
        return _NoteBuild(
            text=transcription.text.strip(),
            prefix=tool.note_prefix,
            cost_usd=transcription.cost_usd,
            duration_seconds=transcription.duration_seconds,
        )
    if tool.kind == "image":
        description = await describe_image(
            url, http=http, api_key=api_key, model=tool.model,
            prompt=tool.prompt or "",
        )
        return _NoteBuild(
            text=description.text, prefix=tool.note_prefix, cost_usd=description.cost_usd,
        )
    if tool.kind == "document":
        description = await describe_document(
            url, http=http, api_key=api_key, model=tool.model,
            prompt=tool.prompt or "",
        )
        return _NoteBuild(
            text=description.text, prefix=tool.note_prefix, cost_usd=description.cost_usd,
        )
    raise ValueError(f"kind no soportado: {tool.kind!r}")
