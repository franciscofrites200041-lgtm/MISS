from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite


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

# Defaults sembrados en la primera corrida. Editables desde el dashboard.
_IMAGE_PROMPT_DEFAULT = (
    "Sos un asistente que describe imágenes para una nota de un CRM. "
    "Respondé SOLO con la nota final, en español, en 3-4 líneas. "
    "Describí qué muestra la imagen. Si hay presupuesto, monto, fecha o "
    "números importantes, mencionalos explícitamente. Sin encabezados ni bullets."
)

_DOCUMENT_PROMPT_DEFAULT = (
    "Sos un asistente que resume documentos para una nota de un CRM. "
    "Respondé SOLO con la nota final, en español, en 3-4 líneas. "
    "Incluí tipo de documento y tema principal. Si hay presupuesto, monto o "
    "fecha, mencionalos explícitamente. Sin encabezados ni bullets."
)

SEED_TOOLS: list[dict] = [
    {
        "slug": "audio",
        "name": "Transcripción de audio",
        "description": "Transcribe notas de voz de Spoter y las manda como nota al CRM.",
        "kind": "audio",
        "enabled": 1,
        "model": "openai/whisper-large-v3-turbo",
        "prompt": None,
        "note_prefix": "[Transcripción de audio]",
    },
    {
        "slug": "image",
        "name": "Descripción de imagen",
        "description": "Describe imágenes de Spoter en 3-4 líneas, resaltando montos y datos importantes.",
        "kind": "image",
        "enabled": 1,
        "model": "google/gemini-2.5-flash",
        "prompt": _IMAGE_PROMPT_DEFAULT,
        "note_prefix": "[Descripción de imagen]",
    },
    {
        "slug": "document",
        "name": "Resumen de documento",
        "description": "Resume PDFs de Spoter. Extrae texto por código primero; si no, usa vision.",
        "kind": "document",
        "enabled": 1,
        "model": "google/gemini-2.5-flash",
        "prompt": _DOCUMENT_PROMPT_DEFAULT,
        "note_prefix": "[Resumen de documento]",
    },
]


@dataclass
class Tool:
    slug: str
    name: str
    description: str
    kind: str
    enabled: bool
    model: str
    prompt: str | None
    note_prefix: str
    updated_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_tool(row) -> Tool:
    d = dict(row)
    d["enabled"] = bool(d["enabled"])
    return Tool(**d)


class ToolsStore:
    """Config persistida de las herramientas. Se siembra la primera vez."""

    def __init__(self, path: Path):
        self.path = Path(path)

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            await db.commit()
            async with db.execute("SELECT COUNT(*) FROM tools") as cur:
                (count,) = await cur.fetchone()
            if count == 0:
                now = _now()
                await db.executemany(
                    "INSERT INTO tools (slug, name, description, kind, enabled, model, "
                    "prompt, note_prefix, updated_at) VALUES "
                    "(:slug, :name, :description, :kind, :enabled, :model, "
                    ":prompt, :note_prefix, :updated_at)",
                    [{**t, "updated_at": now} for t in SEED_TOOLS],
                )
                await db.commit()

    async def list(self) -> list[Tool]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM tools ORDER BY slug"
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_tool(r) for r in rows]

    async def get(self, slug: str) -> Tool | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM tools WHERE slug = ?", (slug,)
            ) as cur:
                row = await cur.fetchone()
        return _row_to_tool(row) if row else None

    async def get_by_kind(self, kind: str) -> Tool | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM tools WHERE kind = ? LIMIT 1", (kind,)
            ) as cur:
                row = await cur.fetchone()
        return _row_to_tool(row) if row else None

    async def update(self, slug: str, **fields) -> Tool | None:
        allowed = {"name", "description", "enabled", "model", "prompt", "note_prefix"}
        clean = {k: v for k, v in fields.items() if k in allowed}
        if not clean:
            return await self.get(slug)
        if "enabled" in clean:
            clean["enabled"] = 1 if clean["enabled"] else 0
        clean["updated_at"] = _now()
        cols = ", ".join(f"{k} = ?" for k in clean)
        values = list(clean.values()) + [slug]
        async with aiosqlite.connect(self.path) as db:
            await db.execute(f"UPDATE tools SET {cols} WHERE slug = ?", values)
            await db.commit()
        return await self.get(slug)

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
