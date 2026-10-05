import pytest
from pydantic import ValidationError

from app.models import SpoterWebhookPayload


REAL_PAYLOAD = {
    "instance": "95",
    "async": 1,
    "datos_conexion": {"mass_url": "https://hub.spoter.com.ar/api/mass.json"},
    "event_type": "transcript_audio",
    "datos_instancias": {
        "instance": "95",
        "phone": "5492615617031",
        "message": {
            "tipo": "audio",
            "propio": 0,
            "fecha_hora": "2026-09-10T08:28:32",
            "mensaje": "",
            "media_url": "https://hub.spoter.com.ar/audio/1753",
        },
    },
}


def test_parses_the_real_spoter_payload():
    payload = SpoterWebhookPayload.model_validate(REAL_PAYLOAD)

    assert payload.event_type == "transcript_audio"
    assert payload.instance == "95"
    assert payload.mass_url == "https://hub.spoter.com.ar/api/mass.json"
    assert len(payload.datos_instancias) == 1

    target = payload.datos_instancias[0]
    assert target.instance == "95"
    assert target.phone == "5492615617031"
    assert target.message is not None
    assert target.message.tipo == "audio"
    assert target.message.media_url == "https://hub.spoter.com.ar/audio/1753"
    assert target.message.mensaje == ""
    assert target.message.propio is False


def test_datos_instancias_accepts_list_form():
    body = dict(REAL_PAYLOAD, datos_instancias=[REAL_PAYLOAD["datos_instancias"]])
    payload = SpoterWebhookPayload.model_validate(body)

    assert len(payload.datos_instancias) == 1
    assert payload.datos_instancias[0].phone == "5492615617031"


def test_coerces_instance_and_phone_to_str():
    body = {
        **REAL_PAYLOAD,
        "instance": 95,
        "datos_instancias": {
            **REAL_PAYLOAD["datos_instancias"],
            "instance": 95,
            "phone": 5492615617031,
        },
    }
    payload = SpoterWebhookPayload.model_validate(body)

    assert payload.instance == "95"
    assert payload.datos_instancias[0].instance == "95"
    assert payload.datos_instancias[0].phone == "5492615617031"


def test_sub_instance_can_differ_from_root_instance():
    body = {
        **REAL_PAYLOAD,
        "instance": "95",
        "datos_instancias": {**REAL_PAYLOAD["datos_instancias"], "instance": "26434"},
    }
    payload = SpoterWebhookPayload.model_validate(body)

    assert payload.instance == "95"
    assert payload.datos_instancias[0].instance == "26434"


def test_message_as_empty_php_array_is_treated_as_absent():
    body = {
        **REAL_PAYLOAD,
        "datos_instancias": {**REAL_PAYLOAD["datos_instancias"], "message": []},
    }
    payload = SpoterWebhookPayload.model_validate(body)

    assert payload.datos_instancias[0].message is None


def test_missing_event_type_is_rejected():
    body = {k: v for k, v in REAL_PAYLOAD.items() if k != "event_type"}

    with pytest.raises(ValidationError):
        SpoterWebhookPayload.model_validate(body)


def test_missing_mass_url_is_rejected():
    body = {**REAL_PAYLOAD, "datos_conexion": {}}

    with pytest.raises(ValidationError):
        SpoterWebhookPayload.model_validate(body)


def _with_message(**message_overrides):
    body = {
        **REAL_PAYLOAD,
        "datos_instancias": {
            **REAL_PAYLOAD["datos_instancias"],
            "message": {
                **REAL_PAYLOAD["datos_instancias"]["message"],
                **message_overrides,
            },
        },
    }
    return SpoterWebhookPayload.model_validate(body)


def test_message_carries_id_original_and_operacion():
    payload = _with_message(id_original="a1b2c3", operacion="42")
    message = payload.datos_instancias[0].message

    assert message.id_original == "a1b2c3"
    assert message.operacion == "42"


def test_id_original_is_not_derived_from_message_id():
    payload = _with_message(id="msg-999")
    message = payload.datos_instancias[0].message

    assert message.id_original is None


def test_operacion_is_not_derived_from_message_id():
    payload = _with_message(id="msg-999")
    message = payload.datos_instancias[0].message

    assert message.operacion is None


def test_operacion_int_is_coerced_to_str():
    payload = _with_message(operacion=42)
    message = payload.datos_instancias[0].message

    assert message.operacion == "42"


def test_malformed_operacion_is_treated_as_absent():
    for malformed in ([], {}, {"nested": True}):
        payload = _with_message(operacion=malformed)
        message = payload.datos_instancias[0].message
        assert message.operacion is None


def test_empty_operacion_is_treated_as_absent():
    payload = _with_message(operacion="  ")
    message = payload.datos_instancias[0].message

    assert message.operacion is None
