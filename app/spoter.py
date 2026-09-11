from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx


_TOKEN_ERROR_RE = re.compile(r"\btoken\b", re.IGNORECASE)


class SpoterAuthError(Exception):
    """Spoter rechazó el token incluso después de un login fresco + retry."""


@dataclass
class TokenCache:
    """(host, instance) -> token en memoria, opcionalmente respaldado por JSON en disco."""

    path: Path | None = None
    _tokens: dict[tuple[str, str], str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        for key, token in (data.get("tokens") or {}).items():
            host, _, instance = key.partition("|")
            if host and instance:
                self._tokens[(host, instance)] = token

    def get(self, host: str, instance: str) -> str | None:
        with self._lock:
            return self._tokens.get((host, instance))

    def set(self, host: str, instance: str, token: str) -> None:
        with self._lock:
            self._tokens[(host, instance)] = token
        self._flush()

    def invalidate(self, host: str, instance: str) -> None:
        with self._lock:
            self._tokens.pop((host, instance), None)
        self._flush()

    def _flush(self) -> None:
        if not self.path:
            return
        with self._lock:
            snapshot = {f"{h}|{i}": t for (h, i), t in self._tokens.items()}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"tokens": snapshot}, indent=2), encoding="utf-8"
            )
        except OSError:
            pass


def _auth_url_for(host: str) -> str:
    return f"https://{host}/api/auth.json"


def _is_token_error_body(body) -> bool:
    if not isinstance(body, dict):
        return False
    for name in ("error", "message"):
        val = body.get(name)
        if isinstance(val, str) and _TOKEN_ERROR_RE.search(val):
            return True
    return False


def _is_auth_fail(response: httpx.Response) -> bool:
    status = response.status_code
    if status in (401, 403):
        return True
    if status in (301, 302, 303, 307, 308):
        location = (response.headers.get("location") or "").lower()
        return "/login" in location or "/auth" in location
    if status == 200:
        try:
            body = response.json()
        except Exception:
            return False
        return _is_token_error_body(body)
    return False


class SpoterClient:
    """Cliente HTTP contra Spoter con auth dinámica, retry ante token vencido y detección
    de la variante 200-con-error-de-token."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        email: str,
        password: str,
        cache: TokenCache,
    ) -> None:
        self._http = http
        self._email = email
        self._password = password
        self._cache = cache

    def set_token(self, host: str, instance: str, token: str) -> None:
        """Seedea el cache con un token ya conocido (ej: el que Spoter manda
        en `datos_conexion.token`). Bypass del login."""
        self._cache.set(host, instance, token)

    async def request(
        self,
        method: str,
        url: str,
        *,
        instance: str,
        **kwargs,
    ) -> httpx.Response:
        host = urlparse(url).netloc

        token = self._cache.get(host, instance) or await self._login(host, instance)
        response = await self._send(method, url, token, **kwargs)
        if not _is_auth_fail(response):
            return response

        self._cache.invalidate(host, instance)
        fresh = await self._login(host, instance)
        response = await self._send(method, url, fresh, **kwargs)
        if _is_auth_fail(response):
            raise SpoterAuthError(
                f"Spoter rechazó {url} incluso tras login fresco (HTTP {response.status_code})."
            )
        return response

    async def _send(
        self, method: str, url: str, token: str, **kwargs
    ) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["X-Csrf-Spoter"] = token
        import logging as _logging
        _logging.getLogger("miss.spoter").info(
            "sending %s %s with X-Csrf-Spoter prefix=%s len=%d",
            method, url, token[:8], len(token),
        )
        return await self._http.request(method, url, headers=headers, **kwargs)

    async def _login(self, host: str, instance: str) -> str:
        # Si SPOTER_API_PASS parece un token estático (>20 chars, sin @) — como
        # lo trata MASS — usarlo directo sin pegarle a /auth.json. Cachea igual
        # para que el retry ante 401 lo invalide y no entre en loop.
        if self._password and len(self._password) > 20 and "@" not in self._password:
            self._cache.set(host, instance, self._password)
            return self._password

        credentials = {
            "email": self._email,
            "instance": instance,
            "password": self._password,
        }
        base_headers = {"Accept": "application/json"}
        url = _auth_url_for(host)

        attempts = (
            {
                "json": credentials,
                "headers": {**base_headers, "Content-Type": "application/json"},
            },
            {"data": credentials, "headers": base_headers},
        )

        for kwargs in attempts:
            try:
                response = await self._http.post(
                    url, follow_redirects=False, timeout=15.0, **kwargs
                )
            except httpx.HTTPError:
                continue

            if response.status_code >= 300:
                continue

            try:
                body = response.json()
            except Exception:
                continue

            token = (
                body.get("token")
                or (body.get("data") or {}).get("token")
                or body.get("access_token")
                or (body.get("data") or {}).get("access_token")
            )
            if token:
                self._cache.set(host, instance, token)
                return token

        raise SpoterAuthError(f"No pude obtener un token desde {url}.")
