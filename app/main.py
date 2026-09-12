from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import AsyncIterator

import logging
import time
from contextvars import ContextVar
from datetime import datetime, timezone

import httpx
from fastapi import (
    BackgroundTasks,
    Body,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
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


# Los loggers de "miss.*" salen a stdout con nivel INFO — así los ve
# `docker logs` / Portainer. Sin esto, uvicorn deja los INFO no configurados
# fuera del root logger que tiene handler.
def _configure_miss_logging() -> None:
    logger = logging.getLogger("miss")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)
        logger.propagate = False


_configure_miss_logging()


# ContextVar propagado al background task: cada request outbound del pipeline
# se registra en la run activa vía event_hooks del httpx.AsyncClient.
_current_run: ContextVar[tuple[str, object] | None] = ContextVar(
    "miss_current_run", default=None,
)


_BODY_PREVIEW_MAX = 4096  # bytes; audios base64 y PDFs no entran acá, y está bien.


def _preview_bytes(raw: bytes | None) -> str | None:
    if not raw:
        return None
    truncated = len(raw) > _BODY_PREVIEW_MAX
    snippet = raw[:_BODY_PREVIEW_MAX]
    try:
        text = snippet.decode("utf-8")
    except UnicodeDecodeError:
        return f"<{len(raw)} bytes binarios>"
    if truncated:
        text += f"\n… (truncado, total {len(raw)} bytes)"
    return text


async def _record_outbound(response: httpx.Response) -> None:
    ctx = _current_run.get()
    if ctx is None:
        return
    run_id, store = ctx
    try:
        ms = int(response.elapsed.total_seconds() * 1000) if response.elapsed else None
    except Exception:
        ms = None
    req = response.request
    await store.append_outbound_call(run_id, {
        "ts": datetime.now(timezone.utc).isoformat(),
        "method": req.method,
        "url": str(req.url),
        "status": response.status_code,
        "ms": ms,
        "request_body": _preview_bytes(getattr(req, "content", None)),
        "request_headers": {
            k: v for k, v in req.headers.items()
            if k.lower() in {"content-type", "accept", "x-csrf-spoter", "authorization"}
        },
    })
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.model_catalog import ModelCatalog
from app.models import SpoterWebhookPayload
from app.pipeline import PipelineConfig, process_webhook
from app.spoter import SpoterClient
from app.storage import RunStore
from app.tools_store import ToolsStore


def _load_config() -> PipelineConfig:
    return PipelineConfig(
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        service_user_id=os.environ.get("MISS_SERVICE_USER_ID", ""),
    )


def _build_spoter(http: httpx.AsyncClient) -> SpoterClient:
    return SpoterClient(http)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    http = httpx.AsyncClient(event_hooks={"response": [_record_outbound]})
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

    app.state.catalog = ModelCatalog(
        http=http,
        api_key=app.state.config.openrouter_api_key,
        store=app.state.tools,
    )

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


@app.get("/api/llm/models", dependencies=[Depends(verify_dashboard_auth)])
async def api_llm_models(request: Request, kind: str = Query(...)):
    if kind not in {"audio", "image", "document"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"kind inválido: {kind}")
    result = await request.app.state.catalog.get(kind)
    if result is None:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="catálogo de modelos no disponible")
    models = list(result.models)
    tool = await request.app.state.tools.get_by_kind(kind)
    if tool is not None and not any(m["id"] == tool.model for m in models):
        models.insert(0, {"id": tool.model, "name": tool.model})
    return {
        "kind": kind,
        "models": models,
        "source": result.source,
        "fetched_at": result.fetched_at,
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


_MAX_TEST_SIZE_BYTES = 20 * 1024 * 1024

_IMAGE_TEST_TYPES = {
    "image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp",
}
_IMAGE_TEST_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_DOCUMENT_TEST_TYPE = "application/pdf"

logger_test = logging.getLogger("miss.test")


def _validate_test_file(kind: str, content_type: str | None, filename: str | None, size: int) -> None:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    name = filename or ""
    if size > _MAX_TEST_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"archivo demasiado grande (máx {_MAX_TEST_SIZE_BYTES // (1024 * 1024)} MB)",
        )
    if kind == "audio":
        from app.transcription import _format_from_content_type, _format_from_url
        if _format_from_content_type(ct) is None and _format_from_url(f"https://x/{name}") is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="tipo de audio no soportado",
            )
    elif kind == "image":
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ct not in _IMAGE_TEST_TYPES and ext not in _IMAGE_TEST_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="tipo de imagen no soportado",
            )
    elif kind == "document":
        if ct != _DOCUMENT_TEST_TYPE and not name.lower().endswith(".pdf"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="solo se aceptan PDFs para documentos",
            )


@app.post("/api/tools/{slug}/test", dependencies=[Depends(verify_dashboard_auth)])
async def api_test_tool(
    request: Request,
    slug: str,
    file: UploadFile | None = File(default=None),
    model: str | None = Form(default=None),
):
    from app.transcription import TranscriptionError, transcribe_bytes
    from app.describe import DescriptionError, describe_image_bytes, describe_document_bytes

    tool = await request.app.state.tools.get(slug)
    if tool is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    api_key = request.app.state.config.openrouter_api_key
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OPENROUTER_API_KEY not configured",
        )
    if file is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="archivo requerido",
        )

    raw = await file.read()
    await file.close()
    _validate_test_file(tool.kind, file.content_type, file.filename, len(raw))

    chosen = (model or "").strip() or tool.model
    http = request.app.state.http

    text: str
    cost_usd: float | None
    duration_seconds: float | None
    latency_ms: float | None

    try:
        if tool.kind == "audio":
            result = await transcribe_bytes(
                raw, http=http, api_key=api_key, model=chosen,
                content_type=file.content_type, filename=file.filename,
            )
            text = result.text
            cost_usd = result.cost_usd
            duration_seconds = result.duration_seconds
            latency_ms = result.latency_ms
        else:
            if tool.kind == "image":
                description = await describe_image_bytes(
                    raw, http=http, api_key=api_key, model=chosen,
                    prompt=tool.prompt or "", content_type=file.content_type,
                )
            else:  # document
                description = await describe_document_bytes(
                    raw, http=http, api_key=api_key, model=chosen,
                    prompt=tool.prompt or "", content_type=file.content_type,
                    filename=file.filename,
                )
            text = description.text
            cost_usd = description.cost_usd
            duration_seconds = None
            latency_ms = description.latency_ms
    except (TranscriptionError, DescriptionError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    logger_test.info(
        "tool test slug=%s kind=%s file=%s size=%d model=%s cost=%s latency_ms=%s dur=%s",
        slug, tool.kind, file.filename, len(raw), chosen, cost_usd, latency_ms, duration_seconds,
    )
    return {
        "model": chosen,
        "text": text,
        "cost_usd": cost_usd,
        "duration_seconds": duration_seconds,
        "latency_ms": latency_ms,
        "file": {
            "name": file.filename,
            "size": len(raw),
            "content_type": file.content_type,
        },
    }


def verify_webhook_secret(x_webhook_secret: str | None = Header(default=None)) -> None:
    # ponytail: si WEBHOOK_SECRET está vacío, webhook queda abierto.
    # Upgrade path: seteá el secret en Portainer y Spoter lo manda en el header.
    expected = os.environ.get("WEBHOOK_SECRET", "").strip()
    if not expected:
        return
    if x_webhook_secret != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


async def _run_pipeline(
    payload: SpoterWebhookPayload,
    raw_body: dict,
    app: FastAPI,
) -> None:
    await process_webhook(
        payload,
        raw_payload=raw_body,
        http=app.state.http,
        spoter=app.state.spoter,
        config=app.state.config,
        tools=app.state.tools,
        store=app.state.store,
        run_context=_current_run,
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
    try:
        raw_body = await request.json()
    except Exception:
        raw_body = {}
    background.add_task(_run_pipeline, payload, raw_body, request.app)
    return {"status": "accepted"}
