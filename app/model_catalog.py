from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx


OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


@dataclass(frozen=True)
class CatalogResult:
    kind: str
    models: list[dict]
    source: str  # "live" | "cache" | "stale" | "snapshot"
    fetched_at: str | None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ModelCatalog:
    """Catálogo de modelos de OpenRouter filtrado por tarea.

    Consulta /api/v1/models (y ?output_modalities=transcription para audio),
    cachea en memoria con TTL y persiste un snapshot en SQLite como fallback."""

    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        api_key: str = "",
        store=None,
        ttl_seconds: float = 600,
    ) -> None:
        self._http = http
        self._api_key = api_key
        self._store = store
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[list[dict], float]] = {}

    async def get(self, kind: str) -> CatalogResult | None:
        cached = self._cache.get(kind)
        if cached is not None and time.monotonic() - cached[1] < self._ttl:
            return CatalogResult(kind, cached[0], "cache", None)

        fresh = await self._fetch(kind)
        if fresh is not None:
            self._cache[kind] = (fresh, time.monotonic())
            if self._store is not None:
                await self._store.save_model_snapshot(kind, fresh)
            return CatalogResult(kind, fresh, "live", _now())

        if cached is not None:
            return CatalogResult(kind, cached[0], "stale", None)

        if self._store is not None:
            snapshot = await self._store.load_model_snapshot(kind)
            if snapshot:
                self._cache[kind] = (snapshot, time.monotonic())
                return CatalogResult(kind, snapshot, "snapshot", None)

        return None

    async def _fetch(self, kind: str) -> list[dict] | None:
        url = OPENROUTER_MODELS_URL
        if kind == "audio":
            url += "?output_modalities=transcription"
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            response = await self._http.get(url, headers=headers, timeout=15.0)
        except httpx.HTTPError:
            return None
        if response.status_code >= 400:
            return None
        try:
            data = response.json().get("data") or []
        except Exception:
            return None

        models = []
        for model in data:
            if not isinstance(model, dict) or not model.get("id"):
                continue
            if not self._matches(kind, model):
                continue
            models.append({"id": model["id"], "name": model.get("name") or model["id"]})
        models.sort(key=lambda m: m["id"].lower())
        return models

    @staticmethod
    def _matches(kind: str, model: dict) -> bool:
        if kind == "audio":
            return True
        architecture = model.get("architecture") or {}
        modalities = architecture.get("input_modalities") or []
        if kind == "image":
            return "image" in modalities
        if kind == "document":
            return "file" in modalities
        return False
