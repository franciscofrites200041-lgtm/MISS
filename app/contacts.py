from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlparse

import httpx

from app.spoter import SpoterAuthError, SpoterClient


_NAME_FIELDS = ("nickname", "nombres", "first_name", "alias")


@dataclass(frozen=True)
class Contact:
    name: str


def _is_junk_name(value: str) -> bool:
    return value.strip().lower().startswith("error:")


async def get_contact_by_phone(
    client: SpoterClient,
    *,
    mass_url: str,
    instance: str,
    phone: str,
) -> Contact | None:
    """Consulta Spoter por `phone` y devuelve el nombre a mostrar. None si no
    hay contacto matcheable (404, JSON malformado, sin data, o nombre 'Error:')."""
    parsed = urlparse(mass_url)
    if not parsed.scheme or not parsed.netloc:
        return None

    url = (
        f"{parsed.scheme}://{parsed.netloc}/hynts/getdata.json"
        f"?instance={quote(str(instance))}"
        f"&entity=info"
        f"&phone={quote(str(phone))}"
    )

    try:
        response = await client.request("GET", url, instance=instance)
    except (httpx.HTTPError, SpoterAuthError):
        return None

    if response.status_code >= 400:
        return None

    try:
        raw = response.json()
    except Exception:
        return None

    if not isinstance(raw, dict):
        return None
    data = raw.get("data")
    if not isinstance(data, dict):
        return None

    for field in _NAME_FIELDS:
        candidate = data.get(field)
        if isinstance(candidate, str) and candidate.strip() and not _is_junk_name(candidate):
            return Contact(name=candidate.strip())
    return None
