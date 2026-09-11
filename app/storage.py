from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    instance_root TEXT NOT NULL,
    sub_instance TEXT NOT NULL,
    phone TEXT NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    attachment_kind TEXT,
    attachment_url TEXT,
    transcription_text TEXT,
    transcription_cost_usd REAL,
    transcription_duration_seconds REAL,
    contact_name TEXT,
    mass_id_original TEXT,
    error_message TEXT,
    raw_payload TEXT,
    outbound_calls TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
"""

# Migraciones idempotentes para bases anteriores. SQLite tolera ALTER TABLE
# ADD COLUMN; el try/except cubre el caso "ya existe".
_MIGRATIONS = [
    "ALTER TABLE runs ADD COLUMN raw_payload TEXT",
    "ALTER TABLE runs ADD COLUMN outbound_calls TEXT",
]


@dataclass
class Run:
    id: str
    created_at: str
    updated_at: str
    instance_root: str
    sub_instance: str
    phone: str
    event_type: str
    status: str
    attachment_kind: str | None
    attachment_url: str | None
    transcription_text: str | None
    transcription_cost_usd: float | None
    transcription_duration_seconds: float | None
    contact_name: str | None
    mass_id_original: str | None
    error_message: str | None
    raw_payload: str | None = None
    outbound_calls: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunStore:
    """Repositorio SQLite para las runs del pipeline. Cada webhook_event crea
    una fila que va cambiando de status a medida que el pipeline avanza."""

    def __init__(self, path: Path):
        self.path = Path(path)

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            for stmt in _MIGRATIONS:
                try:
                    await db.execute(stmt)
                except aiosqlite.OperationalError:
                    pass  # columna ya existe
            await db.commit()

    async def create(
        self,
        *,
        instance_root: str,
        sub_instance: str,
        phone: str,
        event_type: str,
    ) -> str:
        run_id = str(uuid.uuid4())
        now = _now()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO runs (id, created_at, updated_at, instance_root, sub_instance, "
                "phone, event_type, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued')",
                (run_id, now, now, instance_root, sub_instance, phone, event_type),
            )
            await db.commit()
        return run_id

    async def _patch(self, run_id: str, **fields) -> None:
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [run_id]
        async with aiosqlite.connect(self.path) as db:
            await db.execute(f"UPDATE runs SET {cols} WHERE id = ?", values)
            await db.commit()

    async def mark_processing(self, run_id: str) -> None:
        await self._patch(run_id, status="processing")

    async def set_attachment(self, run_id: str, *, kind: str, url: str) -> None:
        await self._patch(run_id, attachment_kind=kind, attachment_url=url)

    async def mark_completed(
        self,
        run_id: str,
        *,
        transcription_text: str | None = None,
        transcription_cost_usd: float | None = None,
        transcription_duration_seconds: float | None = None,
        contact_name: str | None = None,
        mass_id_original: str | None = None,
    ) -> None:
        await self._patch(
            run_id,
            status="completed",
            transcription_text=transcription_text,
            transcription_cost_usd=transcription_cost_usd,
            transcription_duration_seconds=transcription_duration_seconds,
            contact_name=contact_name,
            mass_id_original=mass_id_original,
        )

    async def mark_failed(self, run_id: str, *, error_message: str) -> None:
        await self._patch(run_id, status="failed", error_message=error_message)

    async def save_tool_output(
        self,
        run_id: str,
        *,
        text: str,
        cost_usd: float | None,
        duration_seconds: float | None,
    ) -> None:
        """Persiste el resultado del LLM sin cambiar el status. Se llama apenas
        la tool devuelve texto — así el costo queda contabilizado aunque el
        POST a Spoter falle después."""
        await self._patch(
            run_id,
            transcription_text=text,
            transcription_cost_usd=cost_usd,
            transcription_duration_seconds=duration_seconds,
        )

    async def mark_skipped(self, run_id: str, *, reason: str) -> None:
        await self._patch(run_id, status="skipped", error_message=reason)

    async def set_raw_payload(self, run_id: str, payload: dict) -> None:
        await self._patch(run_id, raw_payload=json.dumps(payload, ensure_ascii=False))

    async def append_outbound_call(self, run_id: str, call: dict) -> None:
        """Agrega una fila al JSON array outbound_calls de esta run.
        ponytail: SQLite no tiene json_array_append portable en <3.38, así que
        leemos-mutamos-escribimos. Como cada run es serial (background task),
        no hay race real."""
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT outbound_calls FROM runs WHERE id = ?", (run_id,)
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return
            current = json.loads(row[0]) if row[0] else []
            current.append(call)
            await db.execute(
                "UPDATE runs SET outbound_calls = ?, updated_at = ? WHERE id = ?",
                (json.dumps(current, ensure_ascii=False), _now(), run_id),
            )
            await db.commit()

    async def get(self, run_id: str) -> Run | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)) as cur:
                row = await cur.fetchone()
        return Run(**dict(row)) if row else None

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        kind: str | None = None,
    ) -> list[Run]:
        query = "SELECT * FROM runs"
        wheres = []
        params: list = []
        if status:
            wheres.append("status = ?")
            params.append(status)
        if kind:
            wheres.append("attachment_kind = ?")
            params.append(kind)
        if wheres:
            query += " WHERE " + " AND ".join(wheres)
        query += " ORDER BY created_at DESC, ROWID DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cur:
                rows = await cur.fetchall()
        return [Run(**dict(r)) for r in rows]

    async def metrics(self) -> dict:
        """Agregados globales. Sin ventana temporal (todavía)."""
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT status, COUNT(*), COALESCE(SUM(transcription_cost_usd), 0), "
                "COALESCE(SUM(transcription_duration_seconds), 0) "
                "FROM runs GROUP BY status"
            ) as cur:
                rows = await cur.fetchall()

        summary = {
            "total": 0,
            "completed": 0,
            "failed": 0,
            "queued": 0,
            "processing": 0,
            "skipped": 0,
            "total_cost_usd": 0.0,
            "total_duration_seconds": 0.0,
        }
        for status, count, cost, duration in rows:
            summary["total"] += count
            summary[status] = count
            summary["total_cost_usd"] += cost or 0
            summary["total_duration_seconds"] += duration or 0
        return summary

    async def stats_by_kind(self) -> dict:
        """Agregados por attachment_kind. Devuelve {kind: {total, completed, failed,
        skipped, total_cost_usd, total_duration_seconds}}."""
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT attachment_kind, status, COUNT(*), "
                "COALESCE(SUM(transcription_cost_usd), 0), "
                "COALESCE(SUM(transcription_duration_seconds), 0) "
                "FROM runs WHERE attachment_kind IS NOT NULL "
                "GROUP BY attachment_kind, status"
            ) as cur:
                rows = await cur.fetchall()

        out: dict = {}
        for kind, st, count, cost, duration in rows:
            slot = out.setdefault(kind, {
                "total": 0, "completed": 0, "failed": 0, "skipped": 0,
                "total_cost_usd": 0.0, "total_duration_seconds": 0.0,
            })
            slot["total"] += count
            if st in slot:
                slot[st] = count
            slot["total_cost_usd"] += cost or 0
            slot["total_duration_seconds"] += duration or 0
        return out
