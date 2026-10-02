from __future__ import annotations

import base64
import io
import logging
import time
from dataclasses import dataclass

import httpx


OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_DESCRIPTION_MODEL = "google/gemini-2.5-flash"

# Instrucción fija para resúmenes de PDFs nativos. Se ignora el prompt editable
# de la tool para garantizar el contrato de formato: español, 3-4 líneas, sin
# encabezados ni bullets, mencionando monto/fecha/referencia cuando estén presentes.
_PDF_SUMMARY_INSTRUCTION = (
    "Resumí el documento en español, en exactamente 3 o 4 líneas. "
    "Sin encabezados ni bullets. "
    "Mencioná el monto a pagar, la fecha y la referencia si aparecen en el documento. "
    "Respondé SOLO con el resumen."
)

# Tope determinístico del texto extraído que se envía al modelo, para acotar
# costos. Conservamos el inicio (tipo de documento/contexto) y el final (totales,
# fechas, referencias); el tramo intermedio se omite.
_PDF_TEXT_MAX_CHARS = 8000
_PDF_TEXT_HEAD_CHARS = 6000

# Umbral mínimo de texto extraíble de un PDF para considerarlo "con texto".
# Debajo asumimos escaneado/textless y el adjunto se omite sin llamar al modelo.
_PDF_TEXT_MIN_CHARS = 200

logger = logging.getLogger("miss.describe")


@dataclass(frozen=True)
class Description:
    text: str
    cost_usd: float | None
    model: str
    latency_ms: float | None = None


class DescriptionError(Exception):
    """Wrapper para fallas de descarga o de la API de descripción."""


class DescriptionSkip(Exception):
    """Señal para omitir un adjunto sin costo ni llamada al modelo: no es un error."""


async def _chat(
    *,
    http: httpx.AsyncClient,
    api_key: str,
    model: str,
    content: list[dict],
    timeout: float,
) -> Description:
    started = time.perf_counter()
    try:
        response = await http.post(
            OPENROUTER_CHAT_URL,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": content}],
                # 3-4 líneas caben holgadamente en 200 tokens; corta accidentes.
                "max_tokens": 200,
                "usage": {"include": True},
            },
        )
    except httpx.HTTPError as e:
        raise DescriptionError(f"OpenRouter no respondió: {e}") from e

    try:
        data = response.json()
    except Exception as e:
        raise DescriptionError(
            f"OpenRouter devolvió body no-JSON (HTTP {response.status_code})"
        ) from e

    if response.status_code >= 400 or (isinstance(data, dict) and data.get("error")):
        detail = (data or {}).get("error") if isinstance(data, dict) else None
        raise DescriptionError(
            f"OpenRouter falló (HTTP {response.status_code}): {detail}"
        )

    choices = data.get("choices") or []
    if not choices:
        raise DescriptionError("OpenRouter devolvió choices vacío")
    text = (choices[0].get("message") or {}).get("content") or ""
    usage = data.get("usage") or {}
    try:
        latency_ms = int(response.elapsed.total_seconds() * 1000)
    except RuntimeError:
        latency_ms = int((time.perf_counter() - started) * 1000)
    return Description(
        text=text.strip(),
        cost_usd=usage.get("cost"),
        model=model,
        latency_ms=latency_ms,
    )


async def describe_image(
    image_url: str,
    *,
    http: httpx.AsyncClient,
    api_key: str,
    model: str,
    prompt: str,
    timeout: float = 45.0,
) -> Description:
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": image_url}},
    ]
    return await _chat(
        http=http, api_key=api_key, model=model, content=content, timeout=timeout
    )


async def describe_image_bytes(
    raw: bytes,
    *,
    http: httpx.AsyncClient,
    api_key: str,
    model: str,
    prompt: str,
    content_type: str | None = None,
    timeout: float = 45.0,
) -> Description:
    mime = (content_type or "image/jpeg").split(";", 1)[0].strip().lower() or "image/jpeg"
    b64 = base64.b64encode(raw).decode("ascii")
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
    ]
    return await _chat(
        http=http, api_key=api_key, model=model, content=content, timeout=timeout
    )


def _try_extract_pdf_text(raw: bytes) -> str:
    """Devuelve el texto extraído por pypdf o '' si no hay/falla.
    ponytail: heurística por conteo de caracteres; si el PDF es escaneado o
    no trae texto nativo, el adjunto se omite sin llamar al modelo."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(io.BytesIO(raw))
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        return "\n".join(parts).strip()
    except Exception as e:
        logger.warning("pypdf falló extrayendo texto: %s", e)
        return ""


def _bound_pdf_text(text: str) -> str:
    """Devuelve `text` acotado a _PDF_TEXT_MAX_CHARS conservando inicio y final."""
    if len(text) <= _PDF_TEXT_MAX_CHARS:
        return text
    tail_chars = _PDF_TEXT_MAX_CHARS - _PDF_TEXT_HEAD_CHARS
    omitted = len(text) - _PDF_TEXT_MAX_CHARS
    return (
        f"{text[:_PDF_TEXT_HEAD_CHARS]}\n"
        f"… [{omitted} caracteres omitidos] …\n"
        f"{text[-tail_chars:]}"
    )


async def _describe_pdf_text(
    text: str,
    *,
    http: httpx.AsyncClient,
    api_key: str,
    timeout: float,
) -> Description:
    # Modelo e instrucción se fuerzan para todo resumen de PDF nativo: el costo
    # queda acotado a Flash y el formato del resumen no depende de config editable.
    content = [{
        "type": "text",
        "text": f"{_PDF_SUMMARY_INSTRUCTION}\n\nDocumento:\n{_bound_pdf_text(text)}",
    }]
    return await _chat(
        http=http, api_key=api_key, model=DEFAULT_DESCRIPTION_MODEL,
        content=content, timeout=timeout,
    )


async def describe_document_bytes(
    raw: bytes,
    *,
    http: httpx.AsyncClient,
    api_key: str,
    model: str,  # ignorado: el modelo de resumen de PDF se fuerza a Flash
    prompt: str,  # ignorado: la instrucción de resumen es fija
    content_type: str | None = None,
    filename: str | None = "documento.pdf",
    upload_timeout: float = 60.0,
) -> Description:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    name = (filename or "").split("?", 1)[0].rsplit("/", 1)[-1] or "documento.pdf"
    is_pdf = ct == "application/pdf" or name.lower().endswith(".pdf")

    if not is_pdf:
        raise DescriptionSkip(f"documento no PDF (content-type={content_type!r})")

    text = _try_extract_pdf_text(raw)
    if len(text) < _PDF_TEXT_MIN_CHARS:
        raise DescriptionSkip("PDF sin texto nativo extraíble (escaneado) — se omite")

    return await _describe_pdf_text(
        text, http=http, api_key=api_key, timeout=upload_timeout,
    )


async def describe_document(
    document_url: str,
    *,
    http: httpx.AsyncClient,
    api_key: str,
    model: str,  # ignorado: el modelo de resumen de PDF se fuerza a Flash
    prompt: str,  # ignorado: la instrucción de resumen es fija
    download_timeout: float = 30.0,
    upload_timeout: float = 60.0,
) -> Description:
    try:
        download = await http.get(document_url, timeout=download_timeout)
    except httpx.HTTPError as e:
        raise DescriptionError(f"No se pudo descargar el documento: {e}") from e
    if download.status_code >= 400:
        raise DescriptionError(
            f"Descarga rechazada (HTTP {download.status_code}) para {document_url}"
        )

    ct = (download.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    name = (document_url or "").split("?", 1)[0].rsplit("/", 1)[-1] or "documento.pdf"
    if ct != "application/pdf" and not name.lower().endswith(".pdf"):
        raise DescriptionSkip(
            f"documento no PDF (content-type={download.headers.get('content-type')!r})"
        )

    text = _try_extract_pdf_text(download.content)
    if len(text) < _PDF_TEXT_MIN_CHARS:
        raise DescriptionSkip("PDF sin texto nativo extraíble (escaneado) — se omite")

    return await _describe_pdf_text(
        text, http=http, api_key=api_key, timeout=upload_timeout,
    )
