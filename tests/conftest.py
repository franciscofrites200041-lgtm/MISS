import pytest
import pytest_asyncio


@pytest.fixture(autouse=True)
def _isolate_runs_db(tmp_path, monkeypatch):
    """Cada test corre con su propio SQLite temporal para runs, evitando que
    lifespan cree/mezcle datos en `./data/runs.db` del repo."""
    monkeypatch.setenv("RUNS_DB_PATH", str(tmp_path / "runs.db"))


@pytest_asyncio.fixture
async def tools(tmp_path):
    """ToolsStore inicializado con los seeds default. Cada test que dispatchea
    el pipeline lo pide como fixture."""
    from app.tools_store import ToolsStore
    ts = ToolsStore(tmp_path / "tools.db")
    await ts.init()
    return ts
