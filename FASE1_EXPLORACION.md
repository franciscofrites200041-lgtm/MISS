# MISS — Fase 1: Exploración y plan

> Relevamiento de **MASS** (referencia, solo lectura) y del **MVP de MISS**, más el plan de qué
> portar y qué dejar afuera. Escrito antes de tocar código.
>
> - `MASS_REPO_PATH` = `C:\Users\Luca\MASS`
> - `MVP_MISS_PATH` = `C:\Users\Luca\Desktop\Indice - Urgencia y Conversion\Indice - Urgencia y Conversion\spoter-transcription-middleware`
> - Repo nuevo: `C:\Users\Luca\Desktop\MISS`
>
> Fecha del relevamiento: 2026-09-10

---

## Índice

1. [MASS — mapa del sistema](#1-mass--mapa-del-sistema)
2. [MVP de MISS — estado actual](#2-mvp-de-miss--estado-actual)
3. [Plan propuesto](#3-plan-propuesto)
4. [Preguntas abiertas](#4-preguntas-abiertas--bloqueantes-para-fase-2)
5. [Decisiones que ya tomé](#5-decisiones-que-ya-tomé-y-te-aviso)

---

## 1. MASS — mapa del sistema

**Stack:** Python 3.10 · FastAPI + Uvicorn · Pydantic v2 · httpx async · SQLAlchemy async sobre
SQLite (`aiosqlite`) + Alembic · Qdrant (RAG) · APScheduler.
Docker Compose de 2 servicios (`spoter-aaas-core` + `qdrant`) en VPS con Portainer, detrás de
nginx-proxy-manager (red externa `nginx-proxy-manager_default`).
Entrypoint: `uvicorn api.gateway:app --host 0.0.0.0 --port 8000`.

### 1.1 Recepción y validación de webhooks

| Qué | Dónde | Detalle |
|---|---|---|
| Endpoint | `api/gateway.py:519` | `POST /v1/spoter/webhook` (alias `/v1/webhook`) |
| Auth | `api/gateway.py:530-534` | Header `X-Webhook-Secret` comparado contra env `WEBHOOK_SECRET`. Si la env está vacía **no valida nada**. Sin HMAC ni firma. |
| Dispatch | `api/gateway.py:538` | Por campo `event_type` del body, con `if` en cascada + un dict `EVENT_HANDLERS` para el path legacy |
| Validación | `models/inbound_models.py` | `SpoterWebhookPayload` (pydantic). Campos: `instance`, `phone`, `event_type`, `message`, `history`, `media_url`, `mass_url`, `data`, `datos_instancias` |
| Respuesta | — | Eventos sync devuelven 200; async devuelven 202/`accepted` inmediato y siguen en background |

**Quirks reales que el modelo absorbe** (`models/inbound_models.py:63-93`):

- PHP serializa dicts vacíos como `[]` → hay validators `mode="before"` que normalizan
  `message`, `data` e `history`.
- `history` puede venir como lista plana **o** como `{mensajes, notas_gerente}`.
- `mass_url` llega anidado en `datos_conexion.mass_url` y se promueve a top-level con un
  `model_validator`.
- `instance` y `phone` pueden venir int o string; el backend coerce a str.

**Catálogo de `event_type` que MASS acepta hoy** (`docs/ARQUITECTURA.md` §4.2):

| `event_type` | Feature flag | Sync/Async |
|---|---|---|
| `blueprint_mass` | `BLUEPRINT_INTAKE_ENABLED` | sync (200) |
| `feedback_mass` | `FEEDBACK_INTAKE_ENABLED` | sync (200) |
| `encuesta_final` | `ENCUESTA_FINAL_ENABLED` | sync (200) |
| `handoff_brief` / `urgency_alert` | `MASS_LITE_ENABLED` | sync (200) |
| `full_analysis` | `MASS_FULL_ENABLED` | sync (200) |
| `daily_analysis` | `MASS_NOCHE_ENABLED` | sync (200) |
| `operator_assistant` | `SECRETARIA_ENABLED` | async (202) |
| `conversacion_cerrada` | — | async (queued) |
| `clasificar_intencion` / `escalamiento` | — | async (accepted) |
| `verificar_pago` | — | async (accepted) |

### 1.2 Detección de adjuntos — ya existe, parcialmente

Shape de un mensaje de Spoter (confirmado en `core/spoter_payload.py` y
`core/classify_orchestrator.py:109`):

```python
{
  "tipo": "chat" | "audio" | "image" | "document" | "order" | "location",
  "propio": bool,          # True = lo mandó el negocio, False = el cliente
  "media_url": "https://...",
  "mensaje": "texto",      # "[AUDIO]" es el placeholder cuando tipo=audio
  "fecha_hora": "..."
}
```

Lo que ya está escrito, repartido en tres lugares con criterios distintos:

- **`core/spoter_payload.py::extract_comprobante_from_history()`** — barre `history` buscando
  `tipo in (document, image)` del cliente con `media_url`. Es la lógica más completa, pero está
  sesgada a comprobantes de pago (usa keywords tipo "transferencia", "comprobante").
- **`services/secretaria/intake.py:562-577`** — la extracción de audio más limpia y la más
  cercana a lo que MISS necesita: `media_url` de root → `audio_url` → `message.media_url` cuando
  `message.tipo == "audio"`, descartando el placeholder `"[AUDIO]"`.
- **`services/agentic_orchestrator/orchestrator.py:50`** — heurística por extensión:
  `tipo == "audio" or media_url.endswith((".mp3", ".ogg", ".wav", ".m4a"))`.

No hay un módulo único "detectar adjunto".

### 1.3 El campo `instance` — para qué se usa

Cuatro cosas a la vez:

1. **Multi-tenant / routing:** resuelve el blueprint del negocio
   (`core/blueprint_cache.resolve_blueprint`).
2. **Identidad compuesta:** dos negocios distintos pueden compartir `instance` si viven en hubs
   distintos. `core/blueprint_id.py` arma la key `{hub_slug}--{instance}`
   (ej. `indovina-spoter-com-ar--58797`), derivando el hub del hostname de `mass_url`.
3. **Selección de token:** `SpoterClient._token_for(key)` busca el token per-instance en un pool
   en RAM; si la key es solo `instance` y matchea 2+ composites, devuelve `None` **a propósito**
   antes que mandar el token del hub equivocado.
4. **Presupuesto y circuit breaker:** tope diario de gasto por instancia y breaker de CRM caído,
   ambos keyeados por `instance`.

**Trampa importante** (`services/secretaria/intake.py:540-545`): Spoter manda **dos** instances.

- `instance` top-level → el hub/bot genérico (ej. 95).
- `datos_instancias[].instance` → la sub-instance real del negocio (ej. 26434).

Si consultás contactos con el de root, **Spoter devuelve 403**. Al responder, la sub va reflejada
en `data.instance` (`api/spoter_client.py::sub_instance_from_payload` + `emit_ai_chat`).
`datos_instancias` puede llegar como dict **o** como lista.

### 1.4 Cliente de Spoter — auth y búsqueda de contactos

`api/spoter_client.py` (839 líneas, singleton async con `httpx.AsyncClient`).

**Auth**, en orden de prioridad:

1. Token per-instance del pool en RAM, registrado desde el blueprint
   (`wizard_meta.spoter_token`, que Spoter entrega en `datos_conexion.header_csrf_token`,
   TTL ~90 días).
2. Token estático de `SPOTER_API_PASS` si "parece token" (>20 chars, sin `@`).
3. Token cacheado en disco (`TOKEN_CACHE_PATH`, default `/app/data/auth.json`).
4. Login dinámico: `POST https://hub.spoter.com.ar/api/auth.json` con
   `{email, instance, password}` — intenta primero JSON, después form-urlencoded, nunca sigue
   redirects.

Header en todas las llamadas salientes: **`X-Csrf-Spoter: <token>`**.
Reintento automático una vez ante 401/403 o 302 → `/login`.
Spoter además señala token inválido con **HTTP 200 + `{"error": "invalid 'token' value."}`** —
hay detección explícita de eso (`_es_token_invalido_en_body`).

**Búsqueda de contactos** (`get_contactos`, línea 700):

```
GET {host}/hynts/getdata.json?instance={i}&entity=contactos&apodo={a}&id_user={u}
```

- El host se deriva del `mass_url` (mismo hub).
- Devuelve `{"data": [...]}` o lista directa.
- Cada item trae: `numero`, `nickname`, `nombres`, `alias`, `first_name`, `telefono`, `email`,
  `similitud`.
- Se filtran contactos basura (`alias`/`nickname`/`first_name`/`nombres` que empiezan con
  `"Error:"`, ej. `"Error: Account Not Exists"` → `es_contacto_basura`).
- Se ordena por `similitud` desc.

**Ficha completa por número** (`get_info_cliente`, línea 774):

```
GET {host}/hynts/getdata.json?instance={i}&entity=infoCliente&numero={n}&id_user={u}
```

Devuelve `{"success", "data": {nickname, fec_ult_mensaje, etapa_embudo_actual, notas_gerente,
objetivo, intencion}}`.

> ⚠️ **Esto es lo que más impacta el flujo de MISS:** en MASS **no existe** una búsqueda de
> contacto *por número*. `entity=contactos` busca **por `apodo` (nombre)** — es el camino inverso
> al que necesitamos (SECRETARIA resuelve "Ale Del Pozo" → número). El único endpoint que va
> número → identidad es `entity=infoCliente`, cuyo campo de nombre es `nickname`.
> Ver [pregunta abierta #1](#1-búsqueda-de-contacto-por-número-bloqueante).

### 1.5 Sistema de actions de salida — sí existe, y es el patrón a reutilizar

Un único método de emisión: `SpoterClient.emit_ai_chat()` (`api/spoter_client.py:459`).

```
POST {mass_url}                          ← ej. https://<hub>.spoter.com.ar/api/mass.json
Headers: Content-Type: application/json
         X-Csrf-Spoter: <token de la instance>
```

```json
{
  "instance": "95",
  "phone": "549...",
  "actions": [ {"codigo": "...", "...": "..."} ],
  "data": {"instance": "<sub-instance>"},
  "id_original": "<uuid>",
  "origen": "operator_assistant",
  "_meta": {}
}
```

- `id_original` es **obligatorio** — `emit_ai_chat` lanza `ValueError` si falta. Es un UUID
  generado y persistido por `core/mass_emission_log.persist_emission()` para que el feedback
  posterior de Spoter no quede huérfano.
- `origen` va a nivel raíz para que Spoter discrimine la fuente sin bucear en `data`.
- `_meta` es metadata operacional; `data` queda reservado para campos que Spoter aplica al CRM.

**Contrato de respuesta:** 200 **con `success: true`** es lo único que cuenta como éxito.

- `success` ausente o distinto → `SpoterMassRejected`.
- Body que menciona token → `SpoterAuthInvalid` (no reintentar: sin re-ingerir blueprint no hay
  token nuevo).
- 5xx o error de red → circuit breaker per-instance + DLQ en tabla propia.
- 4xx → se cuenta como server sano (payload malo nuestro), no va a DLQ.

**`agregar_nota` real** (`services/secretaria/intake.py:1230-1247`):

```python
{
  "codigo": "agregar_nota",
  "mensaje_nota": "<texto, pasado por strip_emojis()>",
  "id_user": operador_id,          # string
  "numero_sugerido": "<número>",
  "nombre_sugerido": "<nombre>",   # solo si el contacto se resolvió
  "alternativas": [...]            # top 3 candidatos, opcional
}
```

Extras opcionales que Spoter aceptó para tareas: `tipo`, `titulo`, `due_date`, `confianza`.

> ⚠️ **El código y la doc no coinciden.** `docs/ARQUITECTURA.md:334` dice que el payload de
> `agregar_nota` es `nota`. Lo que realmente se emite en producción es `mensaje_nota` +
> `id_user` + `numero_sugerido` + `nombre_sugerido`. **Los tres campos que necesita MISS existen
> tal cual.** No hay que inventar nada.

**Catálogo completo de actions** (`docs/ARQUITECTURA.md` §4.3):

| Código | Emisor | Payload |
|---|---|---|
| `rta_sugerida` | LITE/FULL/SECRETARIA/NOCHE | `mensaje_sugerido`, `id_rr?` |
| `auto_reply` | LITE | `mensaje_sugerido`, `nivel_confianza`, `evidencia` |
| `iniciar_conv` | FULL | `mensaje_sugerido` |
| `cerrar_conv` / `marcar_abandono` / `marcar_no_apto` | NOCHE/FULL | vacío |
| `programar_envio` | SECRETARIA | `mensaje_envio`, `numero_sugerido`, `due_date`, `id_plantilla` |
| `programar_evento` | SECRETARIA | `mensaje_evento`, `due_date` |
| `agregar_nota` | SECRETARIA | ver arriba |
| `actualizar_datos` | SECRETARIA/NOCHE | `campo`, `valor`, `numero`, `nombre` |
| `consultar_estado` | SECRETARIA (interno) | — |

### 1.6 Hardcodeado vs configurable

| Hardcodeado | Configurable (env) |
|---|---|
| `AUTH_URL = "https://hub.spoter.com.ar/api/auth.json"` | `WEBHOOK_SECRET`, `SPOTER_API_USER/INSTANCE/PASS` |
| Path `/hynts/getdata.json` y los `entity=` | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY` |
| Nombres de tools por string exacto (`"Buscar Clientes por apodo"`, `"Informacion Cliente"`) | `DATABASE_URL`, `TOKEN_CACHE_PATH`, `LOG_DIR` |
| Modelo `whisper-1` y su precio (`0.006`/min) | ~40 feature flags (`*_ENABLED`), modelos LLM por rol, budgets |
| Nombres de action (`agregar_nota`, etc.) | `MASS_INSTANCE_DAILY_LIMIT_USD`, rate limits, `ADMIN_USER/PASS` |

`mass_url` **no** es env: viene por instancia desde el blueprint / webhook.

### 1.7 Transcripción de audio — ya resuelto en MASS

`services/agentic_orchestrator/tools/transcribir_audio.py`:

- Descarga el audio con httpx a memoria (`timeout=60s`).
- Se lo pasa a OpenAI Whisper (`whisper-1`, `response_format="verbose_json"`).
- Devuelve `{texto, duracion_segundos, cost_usd, model}`.
- **El filename del buffer importa** — Whisper infiere el formato de la extensión.
- Techo conocido y documentado en el propio archivo: audios >25MB fallan (límite de la API).

---

## 2. MVP de MISS — estado actual

**Stack:** Next.js 15 (App Router) + React 19 + TypeScript + Tailwind · Firebase Firestore (logs,
métricas, jobs) · Upstash Redis opcional (rate limit y concurrencia compartidos) · OpenRouter
como proveedor de IA. Docker Compose de un servicio, puerto 3005.

Está **bastante más armado de lo esperado**, y viene con una auditoría de seguridad propia
(`AUDITORIA.md`) con 14 hallazgos ya corregidos.

### 2.1 Lo que ya funciona

- **Transcripción de audio** end-to-end vía OpenRouter (`openai/whisper-large-v3-turbo`), con
  descarga única fuera del loop de reintentos, fallback de modelo, presupuesto duro de llamadas
  facturables por request (`CallBudget`) y costo real reportado por el proveedor
  (`usage: {include: true}`). — `lib/providers/openrouter.ts`
- **Visión** para imágenes y PDFs (`lib/providers/vision.ts`), con motor de parseo de PDF
  configurable (`cloudflare-ai` / `mistral-ocr`). Los "próximos pasos" de imágenes y archivos ya
  están medio hechos.
- **Detección de tipo de media** por `explicitType` → `mimeType` → extensión de la URL.
  — `app/api/process/route.ts:18`
- **Modo async + idempotencia**: `computeIdempotencyKey` (hash de `media_url` + `instance` +
  `id_ticket` + `insert_note` + `model`), `claimJob`/`completeJob`/`failJob`, endpoint de polling
  `GET /api/jobs/{id}`.
- **Seguridad seria**: allowlist de hosts de Spoter (cierra exfiltración de credenciales),
  validación anti-SSRF con re-resolución DNS, rate limiting por IP **y** por instancia, límite de
  concurrencia con timeout de cola, PII enmascarada en logs (`maskPhone`), retención TTL.
- **Auth contra Spoter**: `POST https://{host}/api/auth.json` con `{email, instance, password}`,
  token cacheado 12h en memoria, refresh forzado ante 401/403. **Es el mismo mecanismo que MASS.**
- **Multi-instancia** con config por instancia (host / email / password) en Firestore + dashboard.
- Dashboard Next con métricas, logs y settings.

### 2.2 Lo que falta o no coincide con MASS

| Tema | MVP hoy | MASS |
|---|---|---|
| **Modelo de entrada** | API pull: Spoter llama `POST /api/process` con `media_url`, `phone`, `id_ticket`, `instance` | Webhook push con `event_type` + payload de conversación completo |
| **Auth de entrada** | `MIDDLEWARE_API_KEY` opcional (Bearer / `X-Api-Key`) | `X-Webhook-Secret` |
| **Salida a Spoter** | `POST /acciones/post-accion-ticket/{idTicket}` con `{accion: 2, descripcion, data_numero}` — **no usa el sistema de actions** | `POST {mass_url}` con `actions[]` tipadas |
| **Búsqueda de contacto** | No existe | `getdata.json?entity=contactos` |
| **`datos_instancias` / sub-instance** | No lo contempla | Central |
| **`id_original`, `_meta`, `origen`** | No existen | Obligatorio / usados |
| **DLQ, circuit breaker** | No existen | Sí |
| **Identificación del destino** | Por `id_ticket` | Por `phone` + `instance` |

### 2.3 Decisiones de stack del MVP que chocan con MASS

- TypeScript / Next.js **vs** Python / FastAPI.
- Firestore **vs** SQLite + Alembic.
- OpenRouter **vs** OpenAI directo.
- Redis (Upstash) para concurrencia **vs** nada equivalente.

### 2.4 Pendientes que documenta su propia auditoría

- **P-1 (crítico):** el dashboard y los endpoints de administración no tienen autenticación.
- **P-3:** el token de Spoter se cachea por proceso (no compartido entre instancias del server).
- **P-6:** sin tests automatizados ni CI.
- P-2, P-4, P-5, P-7 a P-10: menores (Vercel, polling, componente de 2.269 líneas, métricas, UX).

---

## 3. Plan propuesto

### 3.1 Qué portar de MASS a MISS

| # | Pieza | Origen | Cómo |
|---|---|---|---|
| 1 | **Receptor de webhooks** | `api/gateway.py:519-560` | Endpoint propio, mismo contrato: `X-Webhook-Secret`, dispatch por `event_type`, 202 inmediato + trabajo en background |
| 2 | **Modelo de payload entrante** | `models/inbound_models.py` | Portar el modelo con **todos** los validators de quirks PHP (`[]` → `{}`, promoción de `mass_url` desde `datos_conexion`). Recortar los campos que MISS no usa (`history` completo, `data` del CRM) |
| 3 | **Resolución de instancia** | `api/spoter_client.py::sub_instance_from_payload` + `core/blueprint_id.py` | Portar tal cual: sub-instance gana sobre root, y la composite key `{hub_slug}--{instance}` |
| 4 | **Cliente Spoter (auth)** | `api/spoter_client.py` | Portar el núcleo: `X-Csrf-Spoter`, login `/api/auth.json`, retry ante 401/403/302-login, detección de token inválido en 200 OK. **Dejar afuera** el pool desde blueprints (MISS no ingiere blueprints) |
| 5 | **Búsqueda de contacto** | `get_contactos` / `get_info_cliente` | Portar el armado de URL, el filtro `es_contacto_basura` y el orden por `similitud` — **con la salvedad de la pregunta abierta #1** |
| 6 | **Emisión de actions** | `emit_ai_chat` | Portar completo: shape del POST, `id_original` UUID, `data.instance` con la sub, validación de `success: true`, excepciones tipadas |
| 7 | **Detección de adjuntos** | `services/secretaria/intake.py:562-577` + `core/spoter_payload.py` | **Unificar** las tres implementaciones dispersas en un solo módulo `attachments` con salida `{kind: audio\|image\|document, url, mime, mensaje}` — así imágenes y archivos entran después sin tocar el flujo de audio |
| 8 | **Transcripción** | `transcribir_audio.py` (MASS) + `lib/providers/openrouter.ts` (MVP) | Interfaz `Transcriber` con una implementación concreta. Ver pregunta abierta #4 |

### 3.2 Qué dejar afuera explícitamente

Todo lo específico del análisis de venta de MASS:

- Blueprints e ingesta (`core/blueprint_*`, `blueprints/`).
- Orquestador agentic y sus ~20 módulos (`services/agentic_orchestrator/`).
- Scoring, embudo, hashtags, urgencia, intencionalidad, triage.
- RAG con Qdrant.
- Self-learning, feedback loop, PostDeployMonitor.
- Budget gate y cost ledger de LLM.
- Admin portal y frontend.
- Los cuatro modos MASS (NOCHE / LITE / FULL / SECRETARIA).
- Alembic + las ~15 tablas del schema.

### 3.3 Qué NO extraer como paquete común (por ahora)

Recomiendo **copiar, no compartir**. El código de MASS que necesitamos está entreverado con
imports del god-module (`core.crm_circuit_breaker`, `db.dlq_repo`, `core.blueprint_cache`), así
que extraer un paquete implicaría refactorizar MASS — y MASS es read-only.

Si más adelante el contrato de Spoter cambia y hay que tocarlo en dos lados, ahí propongo el
paquete común con un diff concreto.

### 3.4 Orden de implementación (Fase 2)

1. Esqueleto FastAPI + `POST /v1/spoter/webhook` con `X-Webhook-Secret` → 202.
2. Modelo de payload + extracción de instance / sub-instance / phone.
3. Módulo `attachments` (audio implementado; image / document como stubs registrados).
4. Cliente Spoter: auth + `X-Csrf-Spoter` + retry.
5. Transcripción de audio.
6. Búsqueda de contacto por número → nombre.
7. Armado y envío de la action `agregar_nota`.
8. Dockerfile + docker-compose + `.env.example` propios.
9. Tests del camino feliz + los fallbacks.

---

## 4. Preguntas abiertas — bloqueantes para Fase 2

### 1. Búsqueda de contacto por número (bloqueante)

En MASS no hay lookup por número contra `entity=contactos`: ese endpoint busca por `apodo`
(nombre). Tres caminos:

- **(a) — recomendado.** Usar `entity=infoCliente&numero={n}` y tomar `data.nickname`. Es el
  único precedente real en el código de MASS para ir de número a identidad.
- **(b)** Probar `entity=contactos&numero={n}` asumiendo que Spoter lo soporta aunque MASS nunca
  lo llame así. Habría que confirmarlo con Spoter o probando contra el hub.
- **(c)** ¿Tenés documentación de Spoter de un endpoint de "buscar contactos" que no esté en el
  repo? (algo que te hayan pasado aparte del `API_SPOTER.md`)

### 2. ¿De dónde saca MISS el token y el `mass_url` de cada instancia?

MASS los recibe en el evento `blueprint_mass` y los guarda en `wizard_meta.spoter_token` /
`mass_url`. MISS no va a recibir blueprints. Opciones:

- **(a) — recomendado.** Login dinámico con credenciales de env (`SPOTER_API_USER` / `_PASS`) +
  `mass_url` leído de `datos_conexion.mass_url` del webhook. Es lo que ya hace el MVP y funciona.
- **(b)** Que Spoter incluya `datos_conexion.header_csrf_token` en el webhook a MISS.
- **(c)** Un mapa de instancias configurado en MISS (como el `.instances_config.json` del MVP).

### 3. Stack de MISS

Recomiendo **Python + FastAPI**, mismo stack que MASS: el contrato de Spoter tiene mucha trampa
acumulada (`success: true` dentro de un 200, sub-instance, quirks de PHP, token inválido con 200)
y portar ese código con cambios mínimos vale más que rehacerlo en TypeScript.

El costo es dejar sin usar la parte del MVP que ya funciona: OpenRouter, Firestore, idempotencia,
SSRF, dashboard. La alternativa es quedarse en Next/TS y reescribir el contrato de Spoter desde
cero.

**¿Cuál preferís?**

### 4. Proveedor de transcripción

- El MVP eligió OpenRouter (`whisper-large-v3-turbo`).
- MASS usa OpenAI Whisper directo (`whisper-1`).

Si vamos a Python, propongo **OpenRouter**: es la decisión que ya tomaste en el MVP, sale más
barato y deja cambiar de modelo sin tocar código. **¿Confirmás?**

### 5. `id_user` en las consultas a `getdata.json`

Ambos endpoints (`entity=contactos` y `entity=infoCliente`) lo piden. En SECRETARIA sale del
operador que dicta. En el flujo de MISS el audio lo manda el **cliente**, no un operador.

¿Lo tomo de `data.user.id` del webhook si viene (como hace `_normalizar_body` en
`services/secretaria/intake.py`), o hay un id fijo de servicio para MISS?

### 6. `event_type`

¿Qué valor va a mandar Spoter en el webhook a MISS? ¿Reutiliza uno existente
(`clasificar_intencion`, `escalamiento`) o define uno nuevo?

---

## 5. Decisiones que ya tomé (y te aviso)

1. **Shape de `agregar_nota`:** voy a usar el del **código**
   (`mensaje_nota` / `id_user` / `numero_sugerido` / `nombre_sugerido`), no el de la doc, que dice
   `nota` y está desactualizado.
2. **Prefijo de la nota:** configurable por env, con default `"[Transcripción de audio]"`. En
   MASS no hay precedente para este caso — los prefijos existentes son
   `"[Recordatorio YYYY-MM-DD]"` y `"MASS SECRETARIA — ..."`.

### Fallback pendiente de definir (punto 4.d del pedido original)

Si la búsqueda de contacto falla o no devuelve nombre, propongo:
mandar la action igual, con `numero_sugerido` completo y `nombre_sugerido: ""` (vacío, no
`"Desconocido"`), para que Spoter decida cómo renderizarlo y no se guarde un nombre falso en el
CRM. Lo confirmo con vos antes de implementarlo.
