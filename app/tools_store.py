from __future__ import annotations

import json
from dataclasses import dataclass, fields
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
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_cache (
    kind TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS operator_cache (
    instance TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""

# Defaults sembrados en la primera corrida. Editables desde el dashboard.
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
    },
    {
        "slug": "document",
        "name": "Resumen de documento",
        "description": "Resume PDFs de Spoter. Extrae texto por código primero; si no, usa vision.",
        "kind": "document",
        "enabled": 1,
        "model": "google/gemini-2.5-flash",
        "prompt": _DOCUMENT_PROMPT_DEFAULT,
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
    updated_at: str


_TOOL_FIELDS = tuple(f.name for f in fields(Tool))
_TOOL_SELECT = ", ".join(_TOOL_FIELDS)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_tool(row) -> Tool:
    d = {k: v for k, v in dict(row).items() if k in _TOOL_FIELDS}
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
                    "prompt, updated_at) VALUES "
                    "(:slug, :name, :description, :kind, :enabled, :model, "
                    ":prompt, :updated_at)",
                    [{**t, "updated_at": now} for t in SEED_TOOLS],
                )
                await db.commit()

    async def list(self) -> list[Tool]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT {_TOOL_SELECT} FROM tools ORDER BY slug"
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_tool(r) for r in rows]

    async def get(self, slug: str) -> Tool | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT {_TOOL_SELECT} FROM tools WHERE slug = ?", (slug,)
            ) as cur:
                row = await cur.fetchone()
        return _row_to_tool(row) if row else None

    async def get_by_kind(self, kind: str) -> Tool | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT {_TOOL_SELECT} FROM tools WHERE kind = ? LIMIT 1", (kind,)
            ) as cur:
                row = await cur.fetchone()
        return _row_to_tool(row) if row else None

    async def update(self, slug: str, **fields) -> Tool | None:
        allowed = {"name", "description", "enabled", "model", "prompt"}
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

    async def save_operator_snapshot(
        self, instance: str, operators: dict[str, str], *, fetched_at: datetime | None = None
    ) -> None:
        fetched = (fetched_at or datetime.now(timezone.utc)).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO operator_cache (instance, payload, fetched_at) VALUES (?, ?, ?)",
                (str(instance), json.dumps(operators, ensure_ascii=False), fetched),
            )
            await db.commit()

    async def load_operator_snapshot(self, instance: str) -> tuple[dict[str, str] | None, datetime | None]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT payload, fetched_at FROM operator_cache WHERE instance = ?", (str(instance),)
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            return None, None
        try:
            payload = json.loads(row["payload"])
            fetched_at = datetime.fromisoformat(row["fetched_at"])
        except (TypeError, ValueError):
            return None, None
        if not isinstance(payload, dict):
            return None, None
        operators = {str(key): value for key, value in payload.items() if isinstance(value, str)}
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        return operators, fetched_at
