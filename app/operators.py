from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse

import httpx

from app.spoter import SpoterAuthError, SpoterClient
from app.tools_store import ToolsStore


class OperatorDirectory:
    """Resolves Spoter operator names from a weekly persistent snapshot."""

    def __init__(self, store: ToolsStore, *, ttl: timedelta = timedelta(days=7)) -> None:
        self._store = store
        self._ttl = ttl

    async def get_name(
        self,
        client: SpoterClient,
        *,
        mass_url: str,
        instance: str,
        operator_id: str,
    ) -> str | None:
        snapshot, fetched_at = await self._store.load_operator_snapshot(instance)
        if snapshot is not None and self._is_fresh(fetched_at):
            return snapshot.get(operator_id)

        refreshed = await self._fetch(client, mass_url=mass_url, instance=instance)
        if refreshed is not None:
            await self._store.save_operator_snapshot(instance, refreshed)
            return refreshed.get(operator_id)
        return snapshot.get(operator_id) if snapshot else None

    def _is_fresh(self, fetched_at: datetime | None) -> bool:
        return fetched_at is not None and datetime.now(timezone.utc) - fetched_at < self._ttl

    async def _fetch(
        self, client: SpoterClient, *, mass_url: str, instance: str
    ) -> dict[str, str] | None:
        parsed = urlparse(mass_url)
        if not parsed.scheme or not parsed.netloc:
            return None
        url = (
            f"{parsed.scheme}://{parsed.netloc}/hynts/getdata.json"
            f"?entity=operadores&instance={quote(str(instance))}"
        )
        try:
            response = await client.request("GET", url, instance=instance)
        except (httpx.HTTPError, SpoterAuthError):
            return None
        if response.status_code >= 400:
            return None
        try:
            data = response.json().get("data")
        except Exception:
            return None
        if not isinstance(data, list):
            return None

        operators: dict[str, str] = {}
        for row in data:
            if not isinstance(row, dict):
                continue
            operator_id = row.get("id")
            name = row.get("nombre")
            if isinstance(operator_id, bool) or isinstance(name, bool):
                continue
            if operator_id is None or not isinstance(name, str) or not name.strip():
                continue
            operators[str(operator_id)] = name.strip()
        return operators
