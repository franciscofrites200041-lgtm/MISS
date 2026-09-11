from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import AsyncIterator

import logging

import httpx
from fastapi import (
    BackgroundTasks,
    Body,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    status,
)


# ponytail: uvicorn.access loguea cada GET /health, /api/runs, /api/metrics, /api/tools;
# el dashboard polea cada pocos segundos y ensucia el output. Filtramos solo estos
# paths ruidosos — el POST del webhook y errores siguen apareciendo.
_ACCESS_NOISE = ("/health", "/api/runs", "/api/metrics", "/api/tools")


class _AccessNoiseFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        msg = record.getMessage()
        return not any(f'"GET {p}' in msg for p in _ACCESS_NOISE)


logging.getLogger("uvicorn.access").addFilter(_AccessNoiseFilter())
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.models import SpoterWebhookPayload
from app.pipeline import PipelineConfig, process_webhook
from app.spoter import SpoterClient, TokenCache
from app.storage import RunStore
from app.tools_store import ToolsStore


def _load_config() -> PipelineConfig:
    return PipelineConfig(
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        service_user_id=os.environ.get("MISS_SERVICE_USER_ID", ""),
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

    tools = ToolsStore(runs_db_path)  # misma DB, tabla separada
    await tools.init()
    app.state.tools = tools

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


@app.get("/api/tools", dependencies=[Depends(verify_dashboard_auth)])
async def api_list_tools(request: Request):
    tools = await request.app.state.tools.list()
    stats = await request.app.state.store.stats_by_kind()
    return {
        "items": [
            {**asdict(t), "stats": stats.get(t.kind, _empty_stats())}
            for t in tools
        ]
    }


@app.get("/api/tools/{slug}", dependencies=[Depends(verify_dashboard_auth)])
async def api_get_tool(request: Request, slug: str):
    tool = await request.app.state.tools.get(slug)
    if tool is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    stats = await request.app.state.store.stats_by_kind()
    recent = await request.app.state.store.list(limit=20, kind=tool.kind)
    return {
        **asdict(tool),
        "stats": stats.get(tool.kind, _empty_stats()),
        "recent_runs": [asdict(r) for r in recent],
    }


@app.patch("/api/tools/{slug}", dependencies=[Depends(verify_dashboard_auth)])
async def api_update_tool(request: Request, slug: str, patch: dict = Body(...)):
    if await request.app.state.tools.get(slug) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    updated = await request.app.state.tools.update(slug, **patch)
    return asdict(updated)


def _empty_stats() -> dict:
    return {
        "total": 0, "completed": 0, "failed": 0, "skipped": 0,
        "total_cost_usd": 0.0, "total_duration_seconds": 0.0,
    }


def verify_webhook_secret(x_webhook_secret: str | None = Header(default=None)) -> None:
    # ponytail: si WEBHOOK_SECRET está vacío, webhook queda abierto.
    # Upgrade path: seteá el secret en Portainer y Spoter lo manda en el header.
    expected = os.environ.get("WEBHOOK_SECRET", "").strip()
    if not expected:
        return
    if x_webhook_secret != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


async def _run_pipeline(payload: SpoterWebhookPayload, app: FastAPI) -> None:
    await process_webhook(
        payload,
        http=app.state.http,
        spoter=app.state.spoter,
        config=app.state.config,
        tools=app.state.tools,
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
