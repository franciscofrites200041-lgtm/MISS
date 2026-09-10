from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import httpx
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    status,
)

from app.models import SpoterWebhookPayload
from app.pipeline import PipelineConfig, process_webhook
from app.spoter import SpoterClient, TokenCache
from app.transcription import DEFAULT_MODEL


def _load_config() -> PipelineConfig:
    return PipelineConfig(
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        service_user_id=os.environ.get("MISS_SERVICE_USER_ID", ""),
        note_prefix=os.environ.get("MISS_NOTE_PREFIX", "[Transcripción de audio]"),
        transcription_model=os.environ.get("TRANSCRIPTION_MODEL", DEFAULT_MODEL),
    )


def _build_spoter(http: httpx.AsyncClient) -> SpoterClient:
    cache_path = os.environ.get("TOKEN_CACHE_PATH", "").strip()
    cache = TokenCache(path=Path(cache_path)) if cache_path else TokenCache()
    return SpoterClient(
        http,
        email=os.environ.get("SPOTER_API_USER", ""),
        password=os.environ.get("SPOTER_API_PASS", ""),
        cache=cache,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    http = httpx.AsyncClient()
    app.state.http = http
    app.state.spoter = _build_spoter(http)
    app.state.config = _load_config()
    try:
        yield
    finally:
        await app.state.http.aclose()


app = FastAPI(title="MISS", lifespan=lifespan)


def verify_webhook_secret(x_webhook_secret: str | None = Header(default=None)) -> None:
    expected = os.environ.get("WEBHOOK_SECRET")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="WEBHOOK_SECRET not configured",
        )
    if x_webhook_secret != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


async def _run_pipeline(payload: SpoterWebhookPayload, app: FastAPI) -> None:
    await process_webhook(
        payload,
        http=app.state.http,
        spoter=app.state.spoter,
        config=app.state.config,
    )


@app.post(
    "/v1/spoter/webhook",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_webhook_secret)],
)
async def spoter_webhook(
    payload: SpoterWebhookPayload,
    background: BackgroundTasks,
    request: Request,
):
    background.add_task(_run_pipeline, payload, request.app)
    return {"status": "accepted"}
