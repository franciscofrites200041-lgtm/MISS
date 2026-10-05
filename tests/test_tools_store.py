from dataclasses import asdict

import aiosqlite

from app.tools_store import ToolsStore


async def test_seed_excludes_image_kind(tmp_path):
    """Un seed fresco no debe hacer disponible el procesamiento de imágenes."""
    store = ToolsStore(tmp_path / "tools.db")
    await store.init()

    tools = await store.list()
    kinds = {t.kind for t in tools}

    assert "image" not in kinds
    assert kinds == {"audio", "document"}
    assert await store.get_by_kind("image") is None


async def test_seed_has_no_note_prefix_field(tmp_path):
    """El seed fresco ya no expone note_prefix como setting activo."""
    store = ToolsStore(tmp_path / "tools.db")
    await store.init()

    tools = await store.list()
    assert tools
    for t in tools:
        assert not hasattr(t, "note_prefix")
        assert "note_prefix" not in asdict(t)


async def test_legacy_db_with_note_prefix_column_stays_readable(tmp_path):
    """Una DB legacy que aún tiene la columna tools.note_prefix debe leerse sin
    error, ignorando la columna obsoleta (sin DROP COLUMN)."""
    path = tmp_path / "tools.db"
    async with aiosqlite.connect(path) as db:
        await db.executescript(
            """
            CREATE TABLE tools (
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
            """
        )
        await db.execute(
            "INSERT INTO tools (slug, name, description, kind, enabled, model, "
            "prompt, note_prefix, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "audio",
                "Transcripción de audio",
                "desc",
                "audio",
                1,
                "openai/whisper-large-v3-turbo",
                None,
                "[Legacy prefix]",
                "2024-01-01T00:00:00+00:00",
            ),
        )
        await db.commit()

    store = ToolsStore(path)
    await store.init()  # no debe reseedear (tabla ya tiene filas) ni fallar

    tool = await store.get("audio")
    assert tool is not None
    assert tool.slug == "audio"
    assert tool.name == "Transcripción de audio"
    assert tool.model == "openai/whisper-large-v3-turbo"
    # La columna obsoleta se ignora y no vuelve como setting activo.
    assert not hasattr(tool, "note_prefix")
    assert "note_prefix" not in asdict(tool)


async def test_note_prefix_cannot_be_updated(tmp_path):
    """note_prefix ya no es un campo actualizable ni retornado."""
    store = ToolsStore(tmp_path / "tools.db")
    await store.init()

    updated = await store.update("audio", model="x", note_prefix="[HACKED]")
    assert updated is not None
    assert updated.model == "x"
    assert not hasattr(updated, "note_prefix")
    assert "note_prefix" not in asdict(updated)

    audio = await store.get("audio")
    assert audio is not None
    assert not hasattr(audio, "note_prefix")
    assert "note_prefix" not in asdict(audio)
