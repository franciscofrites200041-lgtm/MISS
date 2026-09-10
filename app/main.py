from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import AsyncIterator

import httpx
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.models import SpoterWebhookPayload
from app.pipeline import PipelineConfig, process_webhook
from app.spoter import SpoterClient, TokenCache
from app.storage import RunStore
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

    runs_db_path = Path(os.environ.get("RUNS_DB_PATH", "./data/runs.db"))
    store = RunStore(runs_db_path)
    await store.init()
    app.state.store = store

    try:
        yield
    finally:
        await app.state.http.aclose()


app = FastAPI(title="MISS", lifespan=lifespan)

_cors_origins_env = os.environ.get("MISS_CORS_ORIGINS", "").strip()
if _cors_origins_env:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in _cors_origins_env.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=["*"],
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


_security = HTTPBasic()


def verify_dashboard_auth(
    credentials: HTTPBasicCredentials = Depends(_security),
) -> None:
    user = os.environ.get("MISS_DASHBOARD_USER")
    pw = os.environ.get("MISS_DASHBOARD_PASS")
    if not user or not pw:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="MISS_DASHBOARD_USER/PASS not configured",
        )
    ok = secrets.compare_digest(credentials.username, user) and \
         secrets.compare_digest(credentials.password, pw)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Basic"},
        )


@app.get("/api/runs", dependencies=[Depends(verify_dashboard_auth)])
async def api_list_runs(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
):
    rows = await request.app.state.store.list(limit=limit, offset=offset, status=status)
    return {"items": [asdict(r) for r in rows], "limit": limit, "offset": offset}


@app.get("/api/runs/{run_id}", dependencies=[Depends(verify_dashboard_auth)])
async def api_get_run(request: Request, run_id: str):
    row = await request.app.state.store.get(run_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return asdict(row)


@app.get("/api/metrics", dependencies=[Depends(verify_dashboard_auth)])
async def api_metrics(request: Request):
    return await request.app.state.store.metrics()


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
        store=app.state.store,
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
