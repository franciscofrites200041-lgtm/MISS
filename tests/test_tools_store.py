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
