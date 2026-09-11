from __future__ import annotations

import logging
import threading
from urllib.parse import urlparse

import httpx


class SpoterAuthError(Exception):
    """No hay token disponible o Spoter rechazó el token."""


class SpoterClient:
    """Cliente HTTP contra Spoter. El token siempre viene del payload del
    webhook (`datos_conexion.token`) y se registra con set_token antes de
    cualquier request. Sin login dinámico, sin fallback: si no hay token o
    Spoter lo rechaza, la request falla."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http
        self._tokens: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()

    def set_token(self, host: str, instance: str, token: str) -> None:
        with self._lock:
            self._tokens[(host, instance)] = token

    async def request(
        self,
        method: str,
        url: str,
        *,
        instance: str,
        **kwargs,
    ) -> httpx.Response:
        host = urlparse(url).netloc
        with self._lock:
            token = self._tokens.get((host, instance))
        if not token:
            raise SpoterAuthError(
                f"No hay token para {host}/{instance}. Spoter debe mandarlo en "
                "datos_conexion.token."
            )
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["X-Csrf-Spoter"] = token
        logging.getLogger("miss.spoter").info(
            "sending %s %s with X-Csrf-Spoter prefix=%s len=%d",
            method, url, token[:8], len(token),
        )
        return await self._http.request(method, url, headers=headers, **kwargs)
