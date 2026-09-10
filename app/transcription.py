from __future__ import annotations

import base64
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx


OPENROUTER_URL = "https://openrouter.ai/api/v1/audio/transcriptions"
DEFAULT_MODEL = "openai/whisper-large-v3-turbo"

_CONTENT_TYPE_TO_FORMAT = {
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/webm": "webm",
    "audio/mp4": "m4a",
    "audio/m4a": "m4a",
    "audio/x-m4a": "m4a",
    "audio/aac": "m4a",
}

_EXTENSION_TO_FORMAT = {
    ".mp3": "mp3",
    ".ogg": "ogg",
    ".opus": "ogg",
    ".wav": "wav",
    ".webm": "webm",
    ".m4a": "m4a",
    ".aac": "m4a",
    ".mp4": "m4a",
}


@dataclass(frozen=True)
class Transcription:
    text: str
    duration_seconds: float | None
    cost_usd: float | None
    model: str


class TranscriptionError(Exception):
    """Wrapper para fallas de descarga o de la API de transcripción."""


def _format_from_content_type(content_type: str | None) -> str | None:
    if not content_type:
        return None
    ct = content_type.split(";", 1)[0].strip().lower()
    return _CONTENT_TYPE_TO_FORMAT.get(ct)


def _format_from_url(url: str) -> str | None:
    path = urlparse(url).path.lower()
    for ext, fmt in _EXTENSION_TO_FORMAT.items():
        if path.endswith(ext):
            return fmt
    return None


async def transcribe(
    audio_url: str,
    *,
    http: httpx.AsyncClient,
    api_key: str,
    model: str = DEFAULT_MODEL,
    default_format: str = "wav",
    download_timeout: float = 30.0,
    upload_timeout: float = 45.0,
) -> Transcription:
    try:
        download = await http.get(audio_url, timeout=download_timeout)
    except httpx.HTTPError as e:
        raise TranscriptionError(f"No se pudo descargar el audio: {e}") from e
    if download.status_code >= 400:
        raise TranscriptionError(
            f"Descarga rechazada (HTTP {download.status_code}) para {audio_url}"
        )

    audio_format = (
        _format_from_content_type(download.headers.get("content-type"))
        or _format_from_url(audio_url)
        or default_format
    )
    audio_b64 = base64.b64encode(download.content).decode("ascii")

    try:
        response = await http.post(
            OPENROUTER_URL,
            timeout=upload_timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "input_audio": {"data": audio_b64, "format": audio_format},
                "usage": {"include": True},
            },
        )
    except httpx.HTTPError as e:
        raise TranscriptionError(f"OpenRouter no respondió: {e}") from e

    try:
        data = response.json()
    except Exception as e:
        raise TranscriptionError(
            f"OpenRouter devolvió body no-JSON (HTTP {response.status_code})"
        ) from e

    if response.status_code >= 400 or (isinstance(data, dict) and data.get("error")):
        detail = (data or {}).get("error") if isinstance(data, dict) else None
        raise TranscriptionError(
            f"OpenRouter falló (HTTP {response.status_code}): {detail}"
        )

    usage = data.get("usage") or {}
    return Transcription(
        text=data.get("text") or "",
        duration_seconds=usage.get("seconds"),
        cost_usd=usage.get("cost"),
        model=model,
    )
