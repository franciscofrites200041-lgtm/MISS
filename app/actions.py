from __future__ import annotations

import uuid

from app.spoter import SpoterClient


class SpoterMassRejected(Exception):
    """Spoter no confirmó la emisión: no vino HTTP 200 con `success: true`.
    El caller debe reintentar / DLQ. No se marca la conversación como emitida."""


async def emit_agregar_nota(
    client: SpoterClient,
    *,
    mass_url: str,
    root_instance: str,
    sub_instance: str,
    phone: str,
    mensaje_nota: str,
    id_user: str,
    nombre_sugerido: str = "",
    origen: str = "miss",
) -> str:
    """Emite la action `agregar_nota` contra `mass_url`. Devuelve el `id_original`
    (UUID) que Spoter usará para correlacionar cualquier feedback posterior.

    Contract (shape del código de MASS, no de docs/ARQUITECTURA):
      - Root `instance` para dispatch, `data.instance` = sub-instance para el CRM.
      - Action `agregar_nota` con `mensaje_nota`, `id_user`, `numero_sugerido`,
        `nombre_sugerido` (omitido si está vacío por §5 del plan).

    Éxito estricto: `HTTP 200 + body.success is True`. Cualquier otra cosa
    es SpoterMassRejected.
    """
    id_original = str(uuid.uuid4())
    action: dict = {
        "codigo": "agregar_nota",
        "mensaje_nota": mensaje_nota,
        "id_user": id_user,
        "numero_sugerido": phone,
    }
    if nombre_sugerido:
        action["nombre_sugerido"] = nombre_sugerido

    body = {
        "instance": root_instance,
        "phone": phone,
        "actions": [action],
        "data": {"instance": sub_instance},
        "id_original": id_original,
        "origen": origen,
        "_meta": {},
    }

    response = await client.request(
        "POST",
        mass_url,
        instance=sub_instance,
        headers={"Content-Type": "application/json"},
        json=body,
    )

    if response.status_code != 200:
        raise SpoterMassRejected(
            f"HTTP {response.status_code} desde {mass_url}: {response.text[:200]}"
        )

    try:
        payload = response.json()
    except Exception as e:
        raise SpoterMassRejected(f"Body no-JSON desde {mass_url}") from e

    if not (isinstance(payload, dict) and payload.get("success") is True):
        raise SpoterMassRejected(
            f"success != True desde {mass_url}: {str(payload)[:200]}"
        )

    return id_original
