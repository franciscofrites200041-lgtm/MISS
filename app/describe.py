from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass

import httpx


OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_DESCRIPTION_MODEL = "google/gemini-2.5-flash"

# Umbral mínimo de texto extraíble de un PDF para considerarlo "con texto".
# Debajo asumimos escaneado y lo mandamos al modelo vision.
_PDF_TEXT_MIN_CHARS = 200

logger = logging.getLogger("miss.describe")


@dataclass(frozen=True)
class Description:
    text: str
    cost_usd: float | None
    model: str


class DescriptionError(Exception):
    """Wrapper para fallas de descarga o de la API de descripción."""


async def _chat(
    *,
    http: httpx.AsyncClient,
    api_key: str,
    model: str,
    content: list[dict],
    timeout: float,
) -> Description:
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
    return Description(
        text=text.strip(),
        cost_usd=usage.get("cost"),
        model=model,
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


def _try_extract_pdf_text(raw: bytes) -> str:
    """Devuelve el texto extraído por pypdf o '' si no hay/falla.
    ponytail: heurística por conteo de caracteres; si el PDF es escaneado
    cae al path vision. Upgrade path: OCR local si el vision LLM sale caro."""
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


async def describe_document(
    document_url: str,
    *,
    http: httpx.AsyncClient,
    api_key: str,
    model: str,
    prompt: str,
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

    content_type = (download.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    is_pdf = content_type == "application/pdf" or document_url.lower().split("?", 1)[0].endswith(".pdf")

    text = _try_extract_pdf_text(download.content) if is_pdf else ""
    if len(text) >= _PDF_TEXT_MIN_CHARS:
        content = [{"type": "text", "text": f"{prompt}\n\nDocumento:\n{text}"}]
        return await _chat(
            http=http, api_key=api_key, model=model, content=content, timeout=upload_timeout
        )

    # PDF sin texto (escaneado) o no-PDF: mandarlo entero al modelo vision.
    if not is_pdf:
        raise DescriptionError(
            f"Tipo de documento no soportado (content-type={content_type!r})"
        )

    b64 = base64.b64encode(download.content).decode("ascii")
    filename = document_url.rsplit("/", 1)[-1].split("?", 1)[0] or "documento.pdf"
    content = [
        {"type": "text", "text": prompt},
        {
            "type": "file",
            "file": {
                "filename": filename,
                "file_data": f"data:application/pdf;base64,{b64}",
            },
        },
    ]
    return await _chat(
        http=http, api_key=api_key, model=model, content=content, timeout=upload_timeout
    )
