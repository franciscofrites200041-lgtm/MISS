import pytest


@pytest.fixture(autouse=True)
def _isolate_runs_db(tmp_path, monkeypatch):
    """Cada test corre con su propio SQLite temporal para runs, evitando que
    lifespan cree/mezcle datos en `./data/runs.db` del repo."""
    monkeypatch.setenv("RUNS_DB_PATH", str(tmp_path / "runs.db"))
