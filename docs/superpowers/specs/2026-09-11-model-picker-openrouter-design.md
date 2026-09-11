# Diseño: selector de modelos OpenRouter en el dashboard

Fecha: 2026-09-11

## Problema

En el editor de herramientas del dashboard, el campo "Modelo de OpenRouter" es un
`<input type="text">` (`dashboard/app/tools/[slug]/editor.tsx`). Hay que escribir
el slug a mano (propenso a errores de tipeo) y no se ve qué modelos hay
disponibles en OpenRouter para cada tarea (audio, imagen, documento).

## Objetivo

- En el editor, reemplazar el input de texto por un dropdown estricto con los
  modelos de OpenRouter **filtrados por la tarea** de la tool:
  - `audio` → modelos de transcripción (whisper-class, endpoint STT dedicado).
  - `image` → modelos con modalidad de entrada `image` (la tool manda `image_url`).
  - `document` → modelos con modalidad de entrada `file` (soporte PDF nativo,
    cubre PDFs escaneados que se mandan como `type: file`).
- El cambio se guarda contra la config ya persistida en SQLite (tabla `tools`,
  campo `model`) vía el PATCH existente → **aplica sin redeploy**.
- El modelo actualmente configurado siempre aparece como opción aunque no esté
  en la lista filtrada, para que el valor guardado nunca quede colgado.

## Enfoque elegido

Enfoque A: endpoint de catálogo en MISS + select en el editor. El catálogo se
consulta desde MISS (que tiene la `OPENROUTER_API_KEY`), se filtra y se cachea.

## Arquitectura

### 1. Módulo nuevo `app/model_catalog.py`

Clase `ModelCatalog`:

- **Fuentes**:
  - `image` / `document` → `GET https://openrouter.ai/api/v1/models` con header
    `Authorization: Bearer <OPENROUTER_API_KEY>`.
  - `audio` → `GET https://openrouter.ai/api/v1/models?output_modalities=transcription`
    (los modelos de transcripción no están en el catálogo default).
- **Filtrado** sobre el campo `architecture.input_modalities` (o en el caso de
  STT, todo lo que devuelve el endpoint transcription):
  - `image` → `"image" in input_modalities`.
  - `document` → `"file" in input_modalities`.
  - `audio` → modelos del endpoint STT sin filtro adicional.
- **Salida**: lista ordenada alfabéticamente de `{id, name}`.
- **Cache**: dict en memoria por kind con TTL de 10 min. Si el refetch falla y
  hay cache viejo, se sirve el cache viejo. Si no hay cache en memoria, se cae
  al snapshot persistido en SQLite.
- **Snapshot en DB**: tabla `model_cache` (kind PK, payload JSON, fetched_at).
  Se escribe cada vez que se obtiene una lista fresca. Sirve de fallback offline
  / tras reinicio.
- **Sin datos**: devuelve `None`, el endpoint responde 502 con mensaje claro.

### 2. `app/tools_store.py`

Agregar la tabla `model_cache` al SCHEMA y los métodos:

- `async def save_model_snapshot(kind, payload: list[dict]) -> None`
- `async def load_model_snapshot(kind) -> list[dict] | None`

Misma DB (`RUNS_DB_PATH`), coherente con el resto del store.

### 3. `app/main.py`

- En `lifespan`: instanciar `ModelCatalog(http=app.state.http,
  api_key=config.openrouter_api_key, store=app.state.tools)` y guardarlo en
  `app.state.catalog`.
- Nuevo endpoint `GET /api/llm/models?kind=audio|image|document` protegido con
  `verify_dashboard_auth`:
  - `kind` inválido → 400.
  - Consulta `app.state.catalog.get(kind)`.
  - Agrega el modelo actual de la tool de ese kind (`tools.get_by_kind(kind)`)
    al inicio de la lista si no figura.
  - Respuesta: `{kind, models: [{id, name}], source: "live"|"cache"|"stale",
    fetched_at}`.
  - Sin datos → 502 `{error: "catálogo de modelos no disponible"}`.

### 4. Dashboard — proxy y selector

- Nueva ruta `dashboard/app/api/models/route.ts` (`GET`): valida `kind`, hace
  proxy a MISS `/api/llm/models` con el basic auth server-side (patrón de
  `dashboard/app/api/tools/[slug]/route.ts` y `lib/api.ts`).
- `dashboard/lib/api.ts`: `fetchModels(kind): Promise<LlmModelsResponse>`.
- `dashboard/lib/types.ts`: tipos `LlmModel {id, name}` y
  `LlmModelsResponse {kind, models, source, fetched_at}`.
- `dashboard/app/tools/[slug]/editor.tsx`: reemplazar el `<input>` del modelo
  (líneas actuales 82-99) por un `<select>`:
  - Estados: `models: LlmModel[] | null`, `loading: bool`, `loadError: string | null`.
  - Al montar: fetch `/api/models?kind=${tool.kind}`.
  - Loading → select disabled con placeholder "Cargando modelos de OpenRouter…".
  - Error → aviso y fallback de un solo item: el modelo actual (`tool.model`),
    para que el guardado siga funcionando.
  - `onChange` → `setModel(value)`. Guardar → mismo PATCH existente
    (`model: model`). Sin redeploy.

## Errores y casos borde

- **OpenRouter caído y sin snapshot**: 502; el editor cae al modelo actual como
  única opción y avisa que no pudo cargar el catálogo.
- **Modelo guardado que ya no figura en la lista**: el server lo agrega siempre;
  si el usuario lo cambia, la selección nueva se persiste y reemplaza.
- **`MISS_DASHBOARD_USER/PASS` sin setear**: el endpoint `/api/llm/models` se
  comporta como el resto del dashboard (500 fail-closed).
- **Kind desconocido**: 400.

## Archivos tocados

- `app/model_catalog.py` (nuevo)
- `app/tools_store.py`
- `app/main.py`
- `dashboard/app/api/models/route.ts` (nuevo)
- `dashboard/lib/api.ts`
- `dashboard/lib/types.ts`
- `dashboard/app/tools/[slug]/editor.tsx`

## Testing

- `tests/test_model_catalog.py`: filtrado unitario con payloads fake de OpenRouter
  (audio/image/document), save/load de snapshot, y fallback a cache viejo.
- Tests de API en `tests/test_dashboard_api.py`:
  - `/api/llm/models` requiere auth.
  - `kind` inválido → 400.
  - Con `ModelCatalog` fake en `app.state.catalog`: devuelve lista filtrada e
    incluye el modelo actual de la tool aunque no esté en la lista.
  - Sin datos disponibles → 502.