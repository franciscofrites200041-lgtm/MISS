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