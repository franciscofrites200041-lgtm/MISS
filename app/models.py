from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator


def _coerce_str(value):
    if value is None:
        return None
    return str(value)


class MessageBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tipo: str | None = None
    propio: bool | None = None
    fecha_hora: str | None = None
    mensaje: str | None = None
    media_url: str | None = None


class DatosInstancia(BaseModel):
    model_config = ConfigDict(extra="ignore")

    instance: str
    phone: str
    message: MessageBody | None = None

    @field_validator("instance", "phone", mode="before")
    @classmethod
    def _coerce_ids(cls, v):
        return _coerce_str(v)

    @field_validator("message", mode="before")
    @classmethod
    def _empty_php_array_to_none(cls, v):
        # PHP serializa un dict vacío como [] — lo tratamos como ausente.
        if isinstance(v, list) and not v:
            return None
        return v


class DatosConexion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mass_url: str


class SpoterWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_type: str
    instance: str
    datos_conexion: DatosConexion
    datos_instancias: list[DatosInstancia]

    @field_validator("instance", mode="before")
    @classmethod
    def _coerce_instance(cls, v):
        return _coerce_str(v)

    @field_validator("datos_instancias", mode="before")
    @classmethod
    def _normalize_datos_instancias(cls, v):
        if isinstance(v, dict):
            return [v]
        return v

    @property
    def mass_url(self) -> str:
        return self.datos_conexion.mass_url
