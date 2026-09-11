# Selector de modelos OpenRouter + Panel de prueba — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar el input de texto del modelo en el dashboard por un dropdown con los modelos de OpenRouter filtrados por tarea, y agregar un panel de prueba que procesa un archivo subido con el modelo elegido mostrando resultado, costo y latencia.

**Architecture:** MISS consulta y filtra el catálogo de OpenRouter (`/api/v1/models`, y `?output_modalities=transcription` para audio), lo cachea en memoria con TTL y guarda un snapshot en SQLite como fallback. Expone `GET /api/llm/models?kind=` y `POST /api/tools/{slug}/test`. El dashboard proxea ambos por rutas Next server-side con basic auth y usa un `<select>` (editor) + un componente `Tester` (upload → resultado).

**Tech Stack:** Python 3.10 / FastAPI / httpx / aiosqlite / pytest (venv Windows: `.venv/Scripts/python.exe`). Next.js 15 / React 19 / Tailwind (dashboard).

---

## Setup (una vez, antes de la Task 1)

- [ ] **Step 1: Instalar dependencias faltantes en el venv**

```bash
.venv/Scripts/pip.exe install "pypdf>=5.0" python-multipart
```

Expected: ambos instalan sin error. `python-multipart` es obligatorio para que FastAPI parsee `UploadFile`/`Form` del endpoint de prueba.

- [ ] **Step 2: Configurar identidad de git local (repo, no global)**

```bash
git config user.name "Francisco"
git config user.email "franciscofrites200041@gmail.com"
```

Expected: sin output. Fuerza la identidad que ya usan los commits existentes.

- [ ] **Step 3: Verificar baseline de tests**

```bash
.venv/Scripts/python.exe -m pytest -q
```

Expected: todos los tests pasan (verde).

---

## Task 1: Snapshot del catálogo en `ToolsStore`

**Files:**
- Modify: `app/tools_store.py`
- Test: `tests/test_model_catalog.py` (nuevo)

- [ ] **Step 1: Write the failing test**

Crear `tests/test_model_catalog.py`:

```python
import pytest

from app.tools_store import ToolsStore


async def test_snapshot_roundtrip(tmp_path):
    store = ToolsStore(tmp_path / "tools.db")
    await store.init()
    payload = [{"id": "google/gemini-2.5-flash", "name": "Gemini 2.5 Flash"}]
    await store.save_model_snapshot("image", payload)
    assert await store.load_model_snapshot("image") == payload


async def test_snapshot_missing_kind_returns_none(tmp_path):
    store = ToolsStore(tmp_path / "tools.db")
    await store.init()
    assert await store.load_model_snapshot("audio") is None


async def test_snapshot_overwrites_previous_value(tmp_path):
    store = ToolsStore(tmp_path / "tools.db")
    await store.init()
    await store.save_model_snapshot("image", [{"id": "a", "name": "alpha"}])
    await store.save_model_snapshot("image", [{"id": "b", "name": "beta"}])
    assert await store.load_model_snapshot("image") == [{"id": "b", "name": "beta"}]


async def test_snapshot_survives_new_store_instance(tmp_path):
    payload = [{"id": "openai/whisper-1", "name": "Whisper"}]
    first = ToolsStore(tmp_path / "tools.db")
    await first.init()
    await first.save_model_snapshot("audio", payload)
    second = ToolsStore(tmp_path / "tools.db")
    await second.init()
    assert await second.load_model_snapshot("audio") == payload
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_model_catalog.py -v`
Expected: FAIL — `AttributeError: 'ToolsStore' object has no attribute 'save_model_snapshot'`

- [ ] **Step 3: Write minimal implementation**

En `app/tools_store.py`:

1. Agregar la tabla al SCHEMA:

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS tools (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    kind TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    model TEXT NOT NULL,
    prompt TEXT,
    note_prefix TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_cache (
    kind TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""
```

2. Agregar los métodos (import `json` arriba, después de los imports existentes con `from pathlib import Path`):

```python
async def save_model_snapshot(self, kind: str, payload: list[dict]) -> None:
    async with aiosqlite.connect(self.path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO model_cache (kind, payload, fetched_at) VALUES (?, ?, ?)",
            (kind, json.dumps(payload, ensure_ascii=False), _now()),
        )
        await db.commit()

async def load_model_snapshot(self, kind: str) -> list[dict] | None:
    async with aiosqlite.connect(self.path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT payload FROM model_cache WHERE kind = ?", (kind,)
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["payload"])
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_model_catalog.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run full suite to check no regressions**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add app/tools_store.py tests/test_model_catalog.py
git commit -m "feat(tools_store): snapshot del catálogo de modelos en model_cache"
```

---

## Task 2: `ModelCatalog` — fetch, filtrado, cache TTL y fallback

**Files:**
- Create: `app/model_catalog.py`
- Test: `tests/test_model_catalog.py`

- [ ] **Step 1: Write the failing test**

Agregar al final de `tests/test_model_catalog.py`:

```python
import httpx

from app.model_catalog import CatalogResult, ModelCatalog
from app.tools_store import ToolsStore


MODELS_PAYLOAD = {
    "data": [
        {"id": "openai/gpt-4o", "name": "GPT-4o",
         "architecture": {"input_modalities": ["text", "image"]}},
        {"id": "claude/claude-3.5-sonnet", "name": "Claude 3.5 Sonnet",
         "architecture": {"input_modalities": ["text", "image"]}},
        {"id": "google/gemini-2.5-flash", "name": "Gemini 2.5 Flash",
         "architecture": {"input_modalities": ["text", "image", "file", "audio"]}},
        {"id": "openai/gpt-4o-mini", "name": "GPT-4o Mini",
         "architecture": {"input_modalities": ["text"]}},
        {"id": "anthropic/claude-sonnet-4", "name": "Claude Sonnet 4",
         "architecture": {"input_modalities": ["text", "file"]}},
    ]
}


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_image_filter_returns_only_models_with_image_input():
    def handler(request):
        assert request.url.path == "/api/v1/models"
        assert "output_modalities" not in request.url.query
        return httpx.Response(200, json=MODELS_PAYLOAD)

    http = _client(handler)
    async with http:
        result = await ModelCatalog(http=http, api_key="sk", store=None).get("image")

    assert result.source == "live"
    assert [m["id"] for m in result.models] == [
        "claude/claude-3.5-sonnet", "google/gemini-2.5-flash", "openai/gpt-4o",
    ]


async def test_document_filter_returns_only_models_with_file_input():
    http = _client(lambda req: httpx.Response(200, json=MODELS_PAYLOAD))
    async with http:
        result = await ModelCatalog(http=http, api_key="sk", store=None).get("document")

    assert [m["id"] for m in result.models] == [
        "anthropic/claude-sonnet-4", "google/gemini-2.5-flash",
    ]


async def test_audio_uses_transcription_modality_filter():
    def handler(request):
        assert request.url.path == "/api/v1/models"
        assert request.url.query == "output_modalities=transcription"
        return httpx.Response(200, json={
            "data": [
                {"id": "openai/whisper-1", "name": "Whisper"},
                {"id": "openai/whisper-large-v3", "name": "Whisper Large V3"},
            ]
        })

    http = _client(handler)
    async with http:
        result = await ModelCatalog(http=http, api_key="sk", store=None).get("audio")

    assert result.source == "live"
    assert [m["id"] for m in result.models] == [
        "openai/whisper-1", "openai/whisper-large-v3",
    ]


async def test_uses_authorization_header_when_key_present():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=MODELS_PAYLOAD)

    http = _client(handler)
    async with http:
        await ModelCatalog(http=http, api_key="sk-or-abc", store=None).get("image")

    assert seen[0].headers["authorization"] == "Bearer sk-or-abc"


async def test_skips_authorization_when_key_empty():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=MODELS_PAYLOAD)

    http = _client(handler)
    async with http:
        await ModelCatalog(http=http, api_key="", store=None).get("image")

    assert "authorization" not in seen[0].headers


async def test_cached_live_result_within_ttl_does_not_refetch():
    count = {"n": 0}

    def handler(request):
        count["n"] += 1
        return httpx.Response(200, json=MODELS_PAYLOAD)

    http = _client(handler)
    async with http:
        catalog = ModelCatalog(http=http, api_key="sk", store=None)
        first = await catalog.get("image")
        second = await catalog.get("image")

    assert count["n"] == 1
    assert first.models == second.models
    assert second.source == "cache"


async def test_fallback_to_stale_cache_when_refetch_fails():
    state = {"fails": False}

    def handler(request):
        if state["fails"]:
            return httpx.Response(500, json={"error": {"message": "boom"}})
        return httpx.Response(200, json=MODELS_PAYLOAD)

    http = _client(handler)
    async with http:
        # ttl=0 fuerza a considerar el cache vencido → hace refetch.
        catalog = ModelCatalog(http=http, api_key="sk", store=None, ttl_seconds=0)
        first = await catalog.get("image")
        state["fails"] = True
        second = await catalog.get("image")

    assert first.source == "live"
    assert second.source == "stale"
    assert second.models == first.models


async def test_returns_none_when_no_cache_and_fetch_fails():
    def handler(request):
        return httpx.Response(500, json={"error": {"message": "boom"}})

    http = _client(handler)
    async with http:
        catalog = ModelCatalog(http=http, api_key="sk", store=None, ttl_seconds=0)
        assert await catalog.get("image") is None


async def test_snapshot_fallback_when_no_cache(tmp_path):
    store = ToolsStore(tmp_path / "tools.db")
    await store.init()
    await store.save_model_snapshot("image", [{"id": "google/gemini-2.5-flash", "name": "Gemini"}])

    def handler(request):
        return httpx.Response(500, json={"error": {"message": "boom"}})

    http = _client(handler)
    async with http:
        catalog = ModelCatalog(http=http, api_key="sk", store=store, ttl_seconds=0)
        result = await catalog.get("image")

    assert result.source == "snapshot"
    assert result.models == [{"id": "google/gemini-2.5-flash", "name": "Gemini"}]


async def test_persists_live_snapshot_to_db(tmp_path):
    store = ToolsStore(tmp_path / "tools.db")
    await store.init()

    http = _client(lambda req: httpx.Response(200, json=MODELS_PAYLOAD))
    async with http:
        await ModelCatalog(http=http, api_key="sk", store=store).get("image")

    snapshot = await store.load_model_snapshot("image")
    assert snapshot and any(m["id"] == "openai/gpt-4o" for m in snapshot)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_model_catalog.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.model_catalog'`

- [ ] **Step 3: Write minimal implementation**

Crear `app/model_catalog.py`:

```python
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx


OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


@dataclass(frozen=True)
class CatalogResult:
    kind: str
    models: list[dict]
    source: str  # "live" | "cache" | "stale" | "snapshot"
    fetched_at: str | None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ModelCatalog:
    """Catálogo de modelos de OpenRouter filtrado por tarea.

    Consulta /api/v1/models (y ?output_modalities=transcription para audio),
    cachea en memoria con TTL y persiste un snapshot en SQLite como fallback."""

    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        api_key: str = "",
        store=None,
        ttl_seconds: float = 600,
    ) -> None:
        self._http = http
        self._api_key = api_key
        self._store = store
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[list[dict], float]] = {}

    async def get(self, kind: str) -> CatalogResult | None:
        cached = self._cache.get(kind)
        if cached is not None and time.monotonic() - cached[1] < self._ttl:
            return CatalogResult(kind, cached[0], "cache", None)

        fresh = await self._fetch(kind)
        if fresh is not None:
            self._cache[kind] = (fresh, time.monotonic())
            if self._store is not None:
                await self._store.save_model_snapshot(kind, fresh)
            return CatalogResult(kind, fresh, "live", _now())

        if cached is not None:
            return CatalogResult(kind, cached[0], "stale", None)

        if self._store is not None:
            snapshot = await self._store.load_model_snapshot(kind)
            if snapshot:
                self._cache[kind] = (snapshot, time.monotonic())
                return CatalogResult(kind, snapshot, "snapshot", None)

        return None

    async def _fetch(self, kind: str) -> list[dict] | None:
        url = OPENROUTER_MODELS_URL
        if kind == "audio":
            url += "?output_modalities=transcription"
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            response = await self._http.get(url, headers=headers, timeout=15.0)
        except httpx.HTTPError:
            return None
        if response.status_code >= 400:
            return None
        try:
            data = response.json().get("data") or []
        except Exception:
            return None

        models = []
        for model in data:
            if not isinstance(model, dict) or not model.get("id"):
                continue
            if not self._matches(kind, model):
                continue
            models.append({"id": model["id"], "name": model.get("name") or model["id"]})
        models.sort(key=lambda m: m["id"].lower())
        return models

    @staticmethod
    def _matches(kind: str, model: dict) -> bool:
        if kind == "audio":
            return True
        architecture = model.get("architecture") or {}
        modalities = architecture.get("input_modalities") or []
        if kind == "image":
            return "image" in modalities
        if kind == "document":
            return "file" in modalities
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_model_catalog.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Run full suite to check no regressions**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add app/model_catalog.py tests/test_model_catalog.py
git commit -m "feat(model_catalog): catálogo OpenRouter filtrado por tarea con cache y snapshot"
```

---

## Task 3: Endpoint `GET /api/llm/models`

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_dashboard_api.py`

- [ ] **Step 1: Write the failing test**

Agregar al final de `tests/test_dashboard_api.py`:

```python
from app.model_catalog import CatalogResult


class _FakeCatalog:
    def __init__(self, result):
        self._result = result

    async def get(self, kind):
        return self._result


def test_llm_models_requires_auth(monkeypatch):
    monkeypatch.setenv("MISS_DASHBOARD_USER", "admin")
    monkeypatch.setenv("MISS_DASHBOARD_PASS", "pw")
    with TestClient(app) as client:
        response = client.get("/api/llm/models?kind=image")
    assert response.status_code == 401


def test_llm_models_rejects_unknown_kind(monkeypatch):
    monkeypatch.setenv("MISS_DASHBOARD_USER", "admin")
    monkeypatch.setenv("MISS_DASHBOARD_PASS", "pw")
    with TestClient(app) as client:
        response = client.get("/api/llm/models?kind=video", headers=AUTH_HEADER)
    assert response.status_code == 400


def test_llm_models_returns_filtered_list_including_current_model(monkeypatch, tools):
    monkeypatch.setenv("MISS_DASHBOARD_USER", "admin")
    monkeypatch.setenv("MISS_DASHBOARD_PASS", "pw")
    fake = _FakeCatalog(CatalogResult(
        kind="audio",
        models=[{"id": "openai/whisper-1", "name": "Whisper"}],
        source="live", fetched_at="2026-09-11T00:00:00+00:00",
    ))
    with TestClient(app) as client:
        client.app.state.catalog = fake
        response = client.get("/api/llm/models?kind=audio", headers=AUTH_HEADER)

    assert response.status_code == 200
    body = response.json()
    ids = [m["id"] for m in body["models"]]
    # El modelo actual sembrado para audio (openai/whisper-large-v3-turbo)
    # debe seguir apareciendo aunque no esté en la lista del catálogo.
    assert ids[0] == "openai/whisper-large-v3-turbo"
    assert body["source"] == "live"


def test_llm_models_returns_502_when_no_data(monkeypatch):
    monkeypatch.setenv("MISS_DASHBOARD_USER", "admin")
    monkeypatch.setenv("MISS_DASHBOARD_PASS", "pw")
    with TestClient(app) as client:
        client.app.state.catalog = _FakeCatalog(None)
        response = client.get("/api/llm/models?kind=image", headers=AUTH_HEADER)
    assert response.status_code == 502
    assert "catálogo" in response.json()["detail"]
```

Nota: el seed de `tools` (fixture de conftest) siembra `openai/whisper-large-v3-turbo` para el kind audio. El endpoint lo agrega al inicio si no figura.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard_api.py -k llm_models -v`
Expected: FAIL — `404 Not Found` (el endpoint no existe)

- [ ] **Step 3: Write minimal implementation**

En `app/main.py`:

1. Importar el catálogo (junto a los imports de `app.*`):

```python
from app.model_catalog import ModelCatalog
```

2. En `lifespan`, después de crear `tools`:

```python
    tools = ToolsStore(runs_db_path)  # misma DB, tabla separada
    await tools.init()
    app.state.tools = tools

    app.state.catalog = ModelCatalog(
        http=http,
        api_key=config.openrouter_api_key,
        store=app.state.tools,
    )
```

3. Agregar el endpoint después de `api_get_tool`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard_api.py -k llm_models -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run full suite to check no regressions**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_dashboard_api.py
git commit -m "feat(main): GET /api/llm/models con catálogo filtrado por tarea"
```

---

## Task 4: Dashboard — proxy de catálogo + lib/api

**Files:**
- Create: `dashboard/app/api/models/route.ts`
- Modify: `dashboard/lib/api.ts`
- Modify: `dashboard/lib/types.ts`

(No hay framework de tests JS en el dashboard; la verificación es `npm run build`.)

- [ ] **Step 1: Tipos nuevos en `dashboard/lib/types.ts`**

Agregar al final de `dashboard/lib/types.ts`:

```ts
export interface LlmModel {
  id: string;
  name: string;
}

export interface LlmModelsResponse {
  kind: string;
  models: LlmModel[];
  source: string;
  fetched_at: string | null;
}

export interface ToolTestFile {
  name: string | null;
  size: number;
  content_type: string | null;
}

export interface ToolTestResult {
  model: string;
  text: string;
  cost_usd: number | null;
  duration_seconds: number | null;
  latency_ms: number | null;
  file: ToolTestFile;
}
```

- [ ] **Step 2: Función `fetchModels` en `dashboard/lib/api.ts`**

1. Agregar `LlmModel`, `LlmModelsResponse`, `ToolTestResult` al import de tipos:

```ts
import type {
  LlmModel,
  LlmModelsResponse,
  MetricsSummary,
  Run,
  RunsPage,
  Tool,
  ToolDetail,
  ToolPatch,
  ToolTestResult,
  ToolsList,
} from "./types";
```

2. Cambiar la condición del Content-Type en `fetchJson` (si el body es un `FormData` no se debe forzar `application/json`):

```ts
      ...(init?.body && typeof init.body === "string"
        ? { "Content-Type": "application/json" }
        : {}),
```

3. Agregar las funciones:

```ts
export async function fetchModels(kind: string): Promise<LlmModelsResponse> {
  return fetchJson<LlmModelsResponse>(
    `/api/llm/models?kind=${encodeURIComponent(kind)}`
  );
}

export async function testTool(
  slug: string,
  formData: FormData
): Promise<ToolTestResult> {
  return fetchJson<ToolTestResult>(
    `/api/tools/${encodeURIComponent(slug)}/test`,
    { method: "POST", body: formData }
  );
}
```

- [ ] **Step 3: Ruta proxy `dashboard/app/api/models/route.ts`**

Crear el archivo:

```ts
import { NextResponse } from "next/server";
import { fetchModels } from "@/lib/api";

const ALLOWED_KINDS = new Set(["audio", "image", "document"]);

export async function GET(req: Request) {
  const kind = new URL(req.url).searchParams.get("kind") || "";
  if (!ALLOWED_KINDS.has(kind)) {
    return NextResponse.json({ error: "invalid kind" }, { status: 400 });
  }
  try {
    const data = await fetchModels(kind);
    return NextResponse.json(data);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: msg }, { status: 502 });
  }
}
```

- [ ] **Step 4: Verificar build del dashboard**

Run: `cd dashboard && npm run build`
Expected: build exitoso (no importa si sale el warning del middleware).

- [ ] **Step 5: Commit**

```bash
git add dashboard/app/api/models/route.ts dashboard/lib/api.ts dashboard/lib/types.ts
git commit -m "feat(dashboard): proxy del catálogo de modelos + fetchModels/testTool"
```

---

## Task 5: Dashboard — `<select>` de modelo en el editor

**Files:**
- Modify: `dashboard/app/tools/[slug]/editor.tsx`

- [ ] **Step 1: Convertir el input en select estricto**

Reemplazar TODO el contenido de `dashboard/app/tools/[slug]/editor.tsx` por:

```tsx
"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Save, Power, Loader2, Check, AlertCircle } from "lucide-react";
import { fetchModels } from "@/lib/api";
import type { LlmModel, ToolDetail } from "@/lib/types";

export default function ToolEditor({ tool }: { tool: ToolDetail }) {
  const router = useRouter();
  const [enabled, setEnabled] = useState(tool.enabled);
  const [model, setModel] = useState(tool.model);
  const [notePrefix, setNotePrefix] = useState(tool.note_prefix);
  const [prompt, setPrompt] = useState(tool.prompt ?? "");
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<
    { kind: "ok" | "error"; msg: string } | null
  >(null);

  const [models, setModels] = useState<LlmModel[] | null>(null);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelsError, setModelsError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchModels(tool.kind)
      .then((data) => {
        if (cancelled) return;
        const fallback: LlmModel = { id: tool.model, name: tool.model };
        const hasCurrent = data.models.some((m) => m.id === tool.model);
        setModels(hasCurrent ? data.models : [fallback, ...data.models]);
      })
      .catch(() => {
        if (cancelled) return;
        setModels([{ id: tool.model, name: tool.model }]);
        setModelsError("No se pudo cargar el catálogo de OpenRouter.");
      })
      .finally(() => {
        if (!cancelled) setModelsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tool.kind, tool.model]);

  const patch = async (fields: Record<string, unknown>) => {
    setSaving(true);
    setFeedback(null);
    try {
      const res = await fetch(`/api/tools/${encodeURIComponent(tool.slug)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(fields),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setFeedback({ kind: "ok", msg: "Guardado" });
      router.refresh();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setFeedback({ kind: "error", msg });
    } finally {
      setSaving(false);
    }
  };

  const toggleEnabled = async () => {
    const next = !enabled;
    setEnabled(next);
    await patch({ enabled: next });
  };

  const saveAll = async () => {
    await patch({
      model,
      note_prefix: notePrefix,
      prompt: tool.kind === "audio" ? null : prompt,
    });
  };

  return (
    <div className="glass-panel rounded-xl">
      <div className="flex items-start justify-between gap-3 border-b border-white/5 px-5 py-4">
        <div>
          <h1 className="text-xl font-semibold">{tool.name}</h1>
          <p className="mt-1 text-sm text-slate-400">{tool.description}</p>
          <div className="mt-2 flex items-center gap-2 text-xs text-slate-500">
            <span className="rounded bg-slate-800/60 px-2 py-0.5 font-mono">
              {tool.kind}
            </span>
            <span className="font-mono">/{tool.slug}</span>
          </div>
        </div>
        <button
          onClick={toggleEnabled}
          disabled={saving}
          className={
            enabled
              ? "flex items-center gap-2 rounded-lg bg-emerald-500/10 px-3 py-1.5 text-sm text-emerald-300 hover:bg-emerald-500/20"
              : "flex items-center gap-2 rounded-lg bg-slate-500/10 px-3 py-1.5 text-sm text-slate-400 hover:bg-slate-500/20"
          }
        >
          <Power className="h-4 w-4" />
          {enabled ? "Habilitada" : "Deshabilitada"}
        </button>
      </div>

      <div className="space-y-5 px-5 py-5">
        <div>
          <label className="mb-1.5 block text-xs uppercase tracking-wide text-slate-500">
            Modelo de OpenRouter
          </label>
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            disabled={modelsLoading}
            className="w-full rounded-lg border border-white/5 bg-black/30 px-3 py-2 text-sm text-slate-100 focus:border-blue-500/50 focus:outline-none disabled:opacity-50"
          >
            {modelsLoading ? (
              <option value={model}>{model}</option>
            ) : (
              (models ?? []).map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))
            )}
          </select>
          {modelsError ? (
            <p className="mt-1 flex items-center gap-1 text-xs text-amber-300">
              <AlertCircle className="h-3.5 w-3.5" />
              {modelsError} Se mantiene el modelo actual configurado.
            </p>
          ) : (
            <p className="mt-1 text-xs text-slate-500">
              Modelos disponibles en OpenRouter para tareas de tipo{" "}
              <span className="font-mono">{tool.kind}</span>. El catálogo se
              refresca cada pocos minutos.
            </p>
          )}
        </div>

        <div>
          <label className="mb-1.5 block text-xs uppercase tracking-wide text-slate-500">
            Prefijo de la nota
          </label>
          <input
            type="text"
            value={notePrefix}
            onChange={(e) => setNotePrefix(e.target.value)}
            className="w-full rounded-lg border border-white/5 bg-black/30 px-3 py-2 text-sm text-slate-100 focus:border-blue-500/50 focus:outline-none"
          />
        </div>

        {tool.kind !== "audio" && (
          <div>
            <label className="mb-1.5 block text-xs uppercase tracking-wide text-slate-500">
              Prompt
            </label>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={8}
              className="w-full rounded-lg border border-white/5 bg-black/30 px-3 py-2 font-mono text-sm text-slate-100 focus:border-blue-500/50 focus:outline-none"
            />
            <p className="mt-1 text-xs text-slate-500">
              Se envía como system/user prompt al modelo. Para documentos, si
              hay texto extraíble por código se anexa como &quot;Documento:
              &lt;texto&gt;&quot;.
            </p>
          </div>
        )}

        <div className="flex items-center justify-between gap-3 border-t border-white/5 pt-4">
          <div className="text-xs text-slate-500">
            Última edición: {new Date(tool.updated_at).toLocaleString("es-AR")}
          </div>
          <div className="flex items-center gap-3">
            {feedback && (
              <span
                className={
                  feedback.kind === "ok"
                    ? "flex items-center gap-1 text-xs text-emerald-300"
                    : "flex items-center gap-1 text-xs text-red-300"
                }
              >
                {feedback.kind === "ok" ? (
                  <Check className="h-3.5 w-3.5" />
                ) : (
                  <AlertCircle className="h-3.5 w-3.5" />
                )}
                {feedback.msg}
              </span>
            )}
            <button
              onClick={saveAll}
              disabled={saving}
              className="flex items-center gap-2 rounded-lg bg-blue-500/20 px-4 py-2 text-sm text-blue-200 hover:bg-blue-500/30 disabled:opacity-50"
            >
              {saving ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Save className="h-4 w-4" />
              )}
              Guardar
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verificar build del dashboard**

Run: `cd dashboard && npm run build`
Expected: build exitoso.

- [ ] **Step 3: Commit**

```bash
git add dashboard/app/tools/[slug]/editor.tsx
git commit -m "feat(dashboard): dropdown estricto de modelos en el editor"
```

---

## Task 6: `transcription.py` — `transcribe_bytes` con latencia

**Files:**
- Modify: `app/transcription.py`
- Test: `tests/test_transcription.py`

- [ ] **Step 1: Write the failing test**

Agregar al final de `tests/test_transcription.py`:

```python
from app.transcription import transcribe_bytes


async def test_transcribe_bytes_sends_bytes_and_reports_latency():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={
            "text": "hola desde bytes",
            "usage": {"seconds": 1.5, "cost": 0.0001},
        })

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with http:
        result = await transcribe_bytes(
            AUDIO_BYTES,
            http=http, api_key="sk-or-abc",
            model="openai/whisper-1",
            content_type="audio/ogg",
            filename="nota.ogg",
        )

    assert result.text == "hola desde bytes"
    assert result.duration_seconds == 1.5
    assert result.cost_usd == 0.0001
    assert result.model == "openai/whisper-1"
    assert result.latency_ms is not None
    body = json.loads(seen[0].content)
    assert body["input_audio"]["data"] == base64.b64encode(AUDIO_BYTES).decode("ascii")
    assert body["input_audio"]["format"] == "ogg"
    assert body["model"] == "openai/whisper-1"
    assert seen[0].headers["authorization"] == "Bearer sk-or-abc"


async def test_transcribe_bytes_format_from_filename_when_no_content_type():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"text": "ok", "usage": {"seconds": 1.0}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with http:
        await transcribe_bytes(
            AUDIO_BYTES, http=http, api_key="sk",
            model="openai/whisper-1", content_type=None,
            filename="nota.m4a",
        )

    body = json.loads(seen[0].content)
    assert body["input_audio"]["format"] == "m4a"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_transcription.py -k transcribe_bytes -v`
Expected: FAIL — `ImportError: cannot import name 'transcribe_bytes'`

- [ ] **Step 3: Write minimal implementation**

En `app/transcription.py`:

1. Agregar `latency_ms` al dataclass (al final, con default):

```python
@dataclass(frozen=True)
class Transcription:
    text: str
    duration_seconds: float | None
    cost_usd: float | None
    model: str
    latency_ms: float | None = None
```

2. Extraer el core de envío en `transcribe_bytes` y reescribir `transcribe` para descargar y delegar. Reemplazar la implementación actual de `transcribe` (líneas 67-129) por:

```python
async def transcribe_bytes(
    audio_bytes: bytes,
    *,
    http: httpx.AsyncClient,
    api_key: str,
    model: str,
    content_type: str | None = None,
    filename: str | None = None,
    default_format: str = "wav",
    upload_timeout: float = 45.0,
) -> Transcription:
    audio_format = (
        _format_from_content_type(content_type)
        or _format_from_url(filename or "")
        or default_format
    )
    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")

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
    latency_ms = int(response.elapsed.total_seconds() * 1000) if response.elapsed else None
    return Transcription(
        text=data.get("text") or "",
        duration_seconds=usage.get("seconds"),
        cost_usd=usage.get("cost"),
        model=model,
        latency_ms=latency_ms,
    )


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
    return await transcribe_bytes(
        download.content,
        http=http, api_key=api_key, model=model,
        content_type=download.headers.get("content-type"),
        filename=audio_url,
        default_format=default_format,
        upload_timeout=upload_timeout,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_transcription.py -v`
Expected: PASS (todos, incluidos los nuevos y los existentes que ya pasaban)

- [ ] **Step 5: Run full suite to check no regressions**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: ALL PASS (el pipeline de audio usa `transcribe` → sigue delegando)

- [ ] **Step 6: Commit**

```bash
git add app/transcription.py tests/test_transcription.py
git commit -m "feat(transcription): transcribe_bytes con latencia; transcribe delega"
```

---

## Task 7: `describe.py` — funciones `*_bytes` con latencia

**Files:**
- Modify: `app/describe.py`
- Test: `tests/test_describe.py` (nuevo)

- [ ] **Step 1: Write the failing test**

Crear `tests/test_describe.py`:

```python
import base64
import json

import httpx

from app import describe
from app.describe import (
    DescriptionError,
    describe_document_bytes,
    describe_image_bytes,
)

IMAGE_BYTES = b"\x89PNG\r\n\x1a\nfake image bytes"
PDF_BYTES = b"%PDF-1.4 fake body"


def _chat_handler(seen, status=200):
    def handler(request):
        seen.append(request)
        return httpx.Response(status, json={
            "choices": [{"message": {"content": "Presupuesto por $50.000."}}],
            "usage": {"cost": 0.0002},
        })

    return handler


async def test_describe_image_bytes_sends_data_uri_and_reports_latency():
    seen = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(_chat_handler(seen)))
    async with http:
        result = await describe_image_bytes(
            IMAGE_BYTES,
            http=http, api_key="sk-or-abc",
            model="google/gemini-2.5-flash",
            prompt="describe",
            content_type="image/png",
        )

    assert result.text == "Presupuesto por $50.000."
    assert result.cost_usd == 0.0002
    assert result.latency_ms is not None
    body = json.loads(seen[0].content)
    content = body["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "describe"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == (
        "data:image/png;base64," + base64.b64encode(IMAGE_BYTES).decode("ascii")
    )


async def test_describe_document_bytes_with_text_skips_file(monkeypatch):
    monkeypatch.setattr(
        describe, "_try_extract_pdf_text",
        lambda raw: "Texto del PDF. " * 50,
    )
    seen = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(_chat_handler(seen)))
    async with http:
        result = await describe_document_bytes(
            PDF_BYTES,
            http=http, api_key="sk",
            model="google/gemini-2.5-flash",
            prompt="resumí",
            content_type="application/pdf",
            filename="nota.pdf",
        )

    assert result.text == "Presupuesto por $50.000."
    body = json.loads(seen[0].content)
    content = body["messages"][0]["content"]
    assert all(part["type"] == "text" for part in content)
    assert "Texto del PDF" in content[0]["text"]


async def test_describe_document_bytes_scanned_pdf_uses_file(monkeypatch):
    monkeypatch.setattr(describe, "_try_extract_pdf_text", lambda raw: "")
    seen = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(_chat_handler(seen)))
    async with http:
        result = await describe_document_bytes(
            PDF_BYTES,
            http=http, api_key="sk",
            model="google/gemini-2.5-flash",
            prompt="resumí",
            content_type="application/pdf",
            filename="nota.pdf",
        )

    assert result.latency_ms is not None
    body = json.loads(seen[0].content)
    content = body["messages"][0]["content"]
    file_part = next(p for p in content if p["type"] == "file")
    assert file_part["file"]["filename"] == "nota.pdf"
    assert file_part["file"]["file_data"].startswith("data:application/pdf;base64,")


async def test_describe_document_bytes_rejects_non_pdf_without_text():
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(500))
    )
    async with http:
        try:
            await describe_document_bytes(
                b"plain text not pdf", http=http, api_key="sk",
                model="google/gemini-2.5-flash", prompt="x",
                content_type="text/plain", filename="nota.txt",
            )
            assert False, "debería haber levantado DescriptionError"
        except DescriptionError as exc:
            assert "no soportado" in str(exc)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_describe.py -v`
Expected: FAIL — `ImportError: cannot import name 'describe_image_bytes'`

- [ ] **Step 3: Write minimal implementation**

En `app/describe.py`:

1. Agregar `latency_ms` al dataclass:

```python
@dataclass(frozen=True)
class Description:
    text: str
    cost_usd: float | None
    model: str
    latency_ms: float | None = None
```

2. Setear `latency_ms` al final de `_chat`:

```python
    choices = data.get("choices") or []
    if not choices:
        raise DescriptionError("OpenRouter devolvió choices vacío")
    text = (choices[0].get("message") or {}).get("content") or ""
    usage = data.get("usage") or {}
    latency_ms = int(response.elapsed.total_seconds() * 1000) if response.elapsed else None
    return Description(
        text=text.strip(),
        cost_usd=usage.get("cost"),
        model=model,
        latency_ms=latency_ms,
    )
```

3. Agregar `describe_image_bytes` después de `describe_image`:

```python
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
```

4. Agregar `describe_document_bytes` y reescribir `describe_document` para descargar y delegar. Reemplazar la implementación de `describe_document` (desde `async def describe_document(` hasta el final del archivo) por:

```python
async def describe_document_bytes(
    raw: bytes,
    *,
    http: httpx.AsyncClient,
    api_key: str,
    model: str,
    prompt: str,
    content_type: str | None = None,
    filename: str | None = "documento.pdf",
    upload_timeout: float = 60.0,
) -> Description:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    is_pdf = ct == "application/pdf" or (filename or "").lower().endswith(".pdf")

    text = _try_extract_pdf_text(raw) if is_pdf else ""
    if len(text) >= _PDF_TEXT_MIN_CHARS:
        content = [{"type": "text", "text": f"{prompt}\n\nDocumento:\n{text}"}]
        return await _chat(
            http=http, api_key=api_key, model=model, content=content, timeout=upload_timeout
        )

    if not is_pdf:
        raise DescriptionError(
            f"Tipo de documento no soportado (content-type={content_type!r})"
        )

    b64 = base64.b64encode(raw).decode("ascii")
    name = filename or "documento.pdf"
    content = [
        {"type": "text", "text": prompt},
        {
            "type": "file",
            "file": {
                "filename": name,
                "file_data": f"data:application/pdf;base64,{b64}",
            },
        },
    ]
    return await _chat(
        http=http, api_key=api_key, model=model, content=content, timeout=upload_timeout
    )


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

    return await describe_document_bytes(
        download.content,
        http=http, api_key=api_key, model=model, prompt=prompt,
        content_type=download.headers.get("content-type"),
        filename=document_url,
        upload_timeout=upload_timeout,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_describe.py tests/test_pipeline.py -v`
Expected: ALL PASS (los nuevos y el pipeline de image/document)

- [ ] **Step 5: Run full suite to check no regressions**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add app/describe.py tests/test_describe.py
git commit -m "feat(describe): describe_image_bytes y describe_document_bytes con latencia"
```

---

## Task 8: Backend — `POST /api/tools/{slug}/test`

**Files:**
- Modify: `app/main.py`
- Modify: `pyproject.toml`
- Test: `tests/test_tool_testing.py` (nuevo)

- [ ] **Step 1: Write the failing test**

Crear `tests/test_tool_testing.py`:

```python
from base64 import b64encode
from unittest import mock

import httpx
from fastapi.testclient import TestClient

from app.main import app

AUTH_HEADER = {"Authorization": "Basic " + b64encode(b"admin:pw").decode()}

AUDIO_BYTES = b"\xff\xfb\x90\x00fake mp3 bytes"
IMAGE_BYTES = b"\x89PNG\r\n\x1a\nfake image bytes"
PDF_BYTES = b"%PDF-1.4 fake body"

AUDIO_RESPONSE = {"text": "hola prueba", "usage": {"seconds": 1.5, "cost": 0.0001}}
CHAT_RESPONSE = {
    "choices": [{"message": {"content": "Resultado de la prueba."}}],
    "usage": {"cost": 0.0002},
}


def _make_client(openrouter_handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(openrouter_handler))


def _openrouter(seen, status=200, chat=False):
    def handler(request):
        seen.append(request)
        if chat:
            return httpx.Response(status, json=CHAT_RESPONSE)
        return httpx.Response(status, json=AUDIO_RESPONSE)

    return handler


def _set_creds(monkeypatch):
    monkeypatch.setenv("MISS_DASHBOARD_USER", "admin")
    monkeypatch.setenv("MISS_DASHBOARD_PASS", "pw")


def test_tool_test_requires_auth(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    with TestClient(app) as client:
        response = client.post("/api/tools/audio/test")
    assert response.status_code == 401


def test_tool_test_returns_404_for_unknown_slug(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    with TestClient(app) as client:
        response = client.post("/api/tools/nope/test", headers=AUTH_HEADER)
    assert response.status_code == 404


def test_tool_test_audio_happy_path(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    seen = []
    with TestClient(app) as client:
        client.app.state.http = _make_client(_openrouter(seen))
        response = client.post(
            "/api/tools/audio/test",
            headers=AUTH_HEADER,
            files={"file": ("nota.mp3", AUDIO_BYTES, "audio/mpeg")},
            data={"model": "openai/whisper-1"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "hola prueba"
    assert body["cost_usd"] == 0.0001
    assert body["duration_seconds"] == 1.5
    assert body["latency_ms"] is not None
    assert body["model"] == "openai/whisper-1"
    assert body["file"]["name"] == "nota.mp3"
    assert body["file"]["size"] == len(AUDIO_BYTES)
    assert body["file"]["content_type"] == "audio/mpeg"
    assert seen[0].url.path == "/api/v1/audio/transcriptions"


def test_tool_test_audio_uses_configured_model_when_omitted(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    with TestClient(app) as client:
        client.app.state.http = _make_client(_openrouter([]))
        response = client.post(
            "/api/tools/audio/test",
            headers=AUTH_HEADER,
            files={"file": ("nota.mp3", AUDIO_BYTES, "audio/mpeg")},
        )
    assert response.status_code == 200
    assert response.json()["model"] == "openai/whisper-large-v3-turbo"


def test_tool_test_audio_rejects_wrong_content_type(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    with TestClient(app) as client:
        client.app.state.http = _make_client(_openrouter([]))
        response = client.post(
            "/api/tools/audio/test",
            headers=AUTH_HEADER,
            files={"file": ("nota.txt", b"hola", "text/plain")},
        )
    assert response.status_code == 400
    assert "audio" in response.json()["detail"]


def test_tool_test_image_sends_data_uri(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    seen = []
    with TestClient(app) as client:
        client.app.state.http = _make_client(_openrouter(seen, chat=True))
        response = client.post(
            "/api/tools/image/test",
            headers=AUTH_HEADER,
            files={"file": ("foto.png", IMAGE_BYTES, "image/png")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "Resultado de la prueba."
    assert body["cost_usd"] == 0.0002
    assert body["latency_ms"] is not None
    assert body["duration_seconds"] is None
    assert seen[0].url.path == "/api/v1/chat/completions"


def test_tool_test_document_scanned_pdf_uses_vision(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    from app import describe as describe_mod
    monkeypatch.setattr(describe_mod, "_try_extract_pdf_text", lambda raw: "")

    seen = []
    with TestClient(app) as client:
        client.app.state.http = _make_client(_openrouter(seen, chat=True))
        response = client.post(
            "/api/tools/document/test",
            headers=AUTH_HEADER,
            files={"file": ("nota.pdf", PDF_BYTES, "application/pdf")},
        )

    assert response.status_code == 200
    assert seen[0].url.path == "/api/v1/chat/completions"


def test_tool_test_document_rejects_non_pdf(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    with TestClient(app) as client:
        client.app.state.http = _make_client(_openrouter([]))
        response = client.post(
            "/api/tools/document/test",
            headers=AUTH_HEADER,
            files={"file": ("nota.txt", b"hola", "text/plain")},
        )
    assert response.status_code == 400
    assert "PDF" in response.json()["detail"]


def test_tool_test_returns_502_when_openrouter_fails(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    with TestClient(app) as client:
        client.app.state.http = _make_client(_openrouter([], status=500))
        response = client.post(
            "/api/tools/audio/test",
            headers=AUTH_HEADER,
            files={"file": ("nota.mp3", AUDIO_BYTES, "audio/mpeg")},
        )
    assert response.status_code == 502


def test_tool_test_size_limit(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    with TestClient(app) as client:
        client.app.state.http = _make_client(_openrouter([]))
        with mock.patch("app.main._MAX_TEST_SIZE_BYTES", 10):
            response = client.post(
                "/api/tools/audio/test",
                headers=AUTH_HEADER,
                files={"file": ("nota.mp3", AUDIO_BYTES, "audio/mpeg")},
            )
    assert response.status_code == 400
    assert "grande" in response.json()["detail"]


def test_tool_test_returns_502_without_api_key(monkeypatch):
    _set_creds(monkeypatch)
    with TestClient(app) as client:
        client.app.state.http = _make_client(_openrouter([]))
        response = client.post(
            "/api/tools/audio/test",
            headers=AUTH_HEADER,
            files={"file": ("nota.mp3", AUDIO_BYTES, "audio/mpeg")},
        )
    assert response.status_code == 502
    assert "OPENROUTER_API_KEY" in response.json()["detail"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tool_testing.py -v`
Expected: FAIL — `405 Method Not Allowed` o `404` para `/api/tools/audio/test` (no existe)

- [ ] **Step 3: Agregar `python-multipart` a `pyproject.toml`**

En `pyproject.toml`, agregar a `dependencies`:

```toml
    "python-multipart>=0.0.9",
```

(In [setup] ya se instaló en el venv; este cambio lo fija para builds futuros.)

- [ ] **Step 4: Write minimal implementation**

En `app/main.py`:

1. Agregar al import de `fastapi` los nombres `File`, `Form`, `UploadFile`:

```python
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
```

2. Agregar constantes y helpers después de `_empty_stats`:

```python
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
```

3. Agregar el endpoint después de `api_update_tool`:

```python
@app.post("/api/tools/{slug}/test", dependencies=[Depends(verify_dashboard_auth)])
async def api_test_tool(
    request: Request,
    slug: str,
    file: UploadFile = File(...),
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tool_testing.py -v`
Expected: PASS (11 tests)

- [ ] **Step 6: Run full suite to check no regressions**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add app/main.py pyproject.toml tests/test_tool_testing.py
git commit -m "feat(main): POST /api/tools/{slug}/test para probar la tool con un archivo"
```

---

## Task 9: Dashboard — proxy de test + componente `Tester`

**Files:**
- Create: `dashboard/app/api/tools/[slug]/test/route.ts`
- Create: `dashboard/app/tools/[slug]/tester.tsx`
- Modify: `dashboard/app/tools/[slug]/page.tsx`
- Modify: `dashboard/lib/api.ts` (ya tiene `testTool` de la Task 4)

- [ ] **Step 1: Ruta proxy `dashboard/app/api/tools/[slug]/test/route.ts`**

Crear el archivo:

```ts
import { NextResponse } from "next/server";
import { testTool } from "@/lib/api";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
) {
  const { slug } = await params;
  const formData = await req.formData();
  try {
    const result = await testTool(slug, formData);
    return NextResponse.json(result);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: msg }, { status: 502 });
  }
}
```

Nota: el body del `Request` en Next 15 se consume con `await req.formData()`, que funciona para `multipart/form-data` enviado desde el browser.

- [ ] **Step 2: Componente `dashboard/app/tools/[slug]/tester.tsx`**

Crear el archivo:

```tsx
"use client";

import { useEffect, useMemo, useState } from "react";
import { Play, Loader2, AlertCircle, FileUp } from "lucide-react";
import { fetchModels, testTool } from "@/lib/api";
import type { LlmModel, ToolTestResult } from "@/lib/types";

const ACCEPT: Record<string, string> = {
  audio: "audio/*",
  image: "image/*",
  document: "application/pdf",
};

const FILE_HINT: Record<string, string> = {
  audio: "mp3, wav, ogg, m4a…",
  image: "png, jpg, webp, gif…",
  document: "solo PDF",
};

function formatBytes(n: number): string {
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${n} B`;
}

export default function Tester({
  slug,
  kind,
  currentModel,
}: {
  slug: string;
  kind: string;
  currentModel: string;
}) {
  const [models, setModels] = useState<LlmModel[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [modelChoice, setModelChoice] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ToolTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchModels(kind)
      .then((data) => {
        if (!cancelled) setModels(data.models);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [kind]);

  const selectedLabel = useMemo(() => {
    if (!modelChoice) return `Modelo configurado (${currentModel})`;
    const found = models.find((m) => m.id === modelChoice);
    return found ? found.name : modelChoice;
  }, [modelChoice, models, currentModel]);

  const run = async () => {
    if (!file) {
      setError("Elegí un archivo primero.");
      setResult(null);
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("model", modelChoice);
      setResult(await testTool(slug, formData));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="glass-panel rounded-xl">
      <div className="flex items-center gap-2 border-b border-white/5 px-5 py-4">
        <FileUp className="h-4 w-4 text-blue-300" />
        <h2 className="text-lg font-semibold">Probar herramienta</h2>
        <span className="ml-auto text-xs text-slate-500">
          Subís un archivo y se procesa con el modelo elegido. No se guarda.
        </span>
      </div>

      <div className="space-y-4 px-5 py-5">
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <label className="mb-1.5 block text-xs uppercase tracking-wide text-slate-500">
              Archivo ({FILE_HINT[kind]})
            </label>
            <input
              type="file"
              accept={ACCEPT[kind]}
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="w-full rounded-lg border border-white/5 bg-black/30 px-3 py-2 text-sm text-slate-100 focus:border-blue-500/50 focus:outline-none file:mr-3 file:rounded file:border-0 file:bg-blue-500/20 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-blue-200"
            />
          </div>

          <div>
            <label className="mb-1.5 block text-xs uppercase tracking-wide text-slate-500">
              Modelo de prueba
            </label>
            <select
              value={modelChoice}
              onChange={(e) => setModelChoice(e.target.value)}
              className="w-full rounded-lg border border-white/5 bg-black/30 px-3 py-2 text-sm text-slate-100 focus:border-blue-500/50 focus:outline-none"
            >
              <option value="">
                Modelo configurado ({currentModel})
              </option>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={run}
            disabled={loading}
            className="flex items-center gap-2 rounded-lg bg-blue-500/20 px-4 py-2 text-sm text-blue-200 hover:bg-blue-500/30 disabled:opacity-50"
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            Probar
          </button>
          {loading && (
            <span className="text-xs text-slate-400">
              Procesando con {selectedLabel}…
            </span>
          )}
        </div>

        {error && (
          <div className="flex items-start gap-2 rounded-lg bg-red-500/10 px-4 py-3 text-sm text-red-300">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {result && (
          <div className="space-y-4">
            <dl className="grid grid-cols-2 gap-3 rounded-lg border border-white/5 bg-black/20 p-4 text-sm md:grid-cols-4">
              <div>
                <dt className="text-xs text-slate-500">Modelo</dt>
                <dd className="mt-0.5 break-all font-mono">{result.model}</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">Costo</dt>
                <dd className="mt-0.5 font-mono">
                  {result.cost_usd != null
                    ? `$${result.cost_usd.toFixed(6)}`
                    : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">Latencia</dt>
                <dd className="mt-0.5 font-mono">
                  {result.latency_ms != null
                    ? `${result.latency_ms} ms`
                    : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">Duración (audio)</dt>
                <dd className="mt-0.5 font-mono">
                  {result.duration_seconds != null
                    ? `${result.duration_seconds.toFixed(1)} s`
                    : "—"}
                </dd>
              </div>
            </dl>

            <div>
              <div className="mb-1.5 flex items-center justify-between text-xs text-slate-500">
                <span>Resultado</span>
                <span className="font-mono">
                  {result.file.name} · {formatBytes(result.file.size)}
                </span>
              </div>
              <pre className="whitespace-pre-wrap break-words rounded-lg border border-white/5 bg-black/30 px-4 py-3 font-mono text-sm text-slate-100">
                {result.text}
              </pre>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Montar `Tester` en `dashboard/app/tools/[slug]/page.tsx`**

En `page.tsx`, importar el componente:

```tsx
import ToolEditor from "./editor";
import Tester from "./tester";
```

Y renderizarlo debajo del grid, cerrando el `</main>`:

```tsx
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
        ...
      </div>

      <div className="mt-6">
        <Tester slug={tool.slug} kind={tool.kind} currentModel={tool.model} />
      </div>
    </main>
  );
}
```

- [ ] **Step 4: Verificar build del dashboard**

Run: `cd dashboard && npm run build`
Expected: build exitoso. Si TypeScript marca el `slug` de `params` como `Promise`, es normal (`{ params }: { params: Promise<{ slug: string }> }` ya está así en las otras rutas).

- [ ] **Step 5: Commit**

```bash
git add "dashboard/app/api/tools/[slug]/test/route.ts" "dashboard/app/tools/[slug]/tester.tsx" "dashboard/app/tools/[slug]/page.tsx"
git commit -m "feat(dashboard): panel de prueba (upload + modelo opcional + resultado/costo/latencia)"
```

---

## Task 10: Verificación final

- [ ] **Step 1: Suite completa de backend**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: ALL PASS (tests originales + 31 nuevos aproximados)

- [ ] **Step 2: Build del dashboard**

Run: `cd dashboard && npm run build`
Expected: build exitoso sin errores de tipos.

- [ ] **Step 3: Estado final**

Run: `git status`
Expected: working tree limpio.

**Fin del plan.**