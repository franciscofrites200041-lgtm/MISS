import base64
import json

import httpx

from app import describe
from app.describe import (
    DescriptionSkip,
    describe_document,
    describe_document_bytes,
    describe_image_bytes,
)

IMAGE_BYTES = b"\x89PNG\r\n\x1a\nfake image bytes"
PDF_BYTES = b"%PDF-1.4 fake body"


def _chat_handler(seen, status=200):
    def handler(request):
        seen.append(request)
        return httpx.Response(status, json={
            "choices": [{"message": {"content": "Presupuesto por $50.000."}}],
            "usage": {"cost": 0.0002},
        })

    return handler


async def test_describe_image_bytes_sends_data_uri_and_reports_latency():
    seen = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(_chat_handler(seen)))
    async with http:
        result = await describe_image_bytes(
            IMAGE_BYTES,
            http=http, api_key="sk-or-abc",
            model="google/gemini-2.5-flash",
            prompt="describe",
            content_type="image/png",
        )

    assert result.text == "Presupuesto por $50.000."
    assert result.cost_usd == 0.0002
    assert result.latency_ms is not None
    body = json.loads(seen[0].content)
    content = body["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "describe"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == (
        "data:image/png;base64," + base64.b64encode(IMAGE_BYTES).decode("ascii")
    )


async def test_describe_document_bytes_with_text_skips_file(monkeypatch):
    monkeypatch.setattr(
        describe, "_try_extract_pdf_text",
        lambda raw: "Texto del PDF. " * 50,
    )
    seen = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(_chat_handler(seen)))
    async with http:
        result = await describe_document_bytes(
            PDF_BYTES,
            http=http, api_key="sk",
            model="google/gemini-2.5-flash",
            prompt="resumí",
            content_type="application/pdf",
            filename="nota.pdf",
        )

    assert result.text == "Presupuesto por $50.000."
    body = json.loads(seen[0].content)
    content = body["messages"][0]["content"]
    assert all(part["type"] == "text" for part in content)
    assert "Texto del PDF" in content[0]["text"]


async def test_describe_document_bytes_textless_pdf_skips_without_llm(monkeypatch):
    monkeypatch.setattr(describe, "_try_extract_pdf_text", lambda raw: "")
    seen = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(_chat_handler(seen)))
    async with http:
        try:
            await describe_document_bytes(
                PDF_BYTES,
                http=http, api_key="sk",
                model="google/gemini-2.5-flash",
                prompt="resumí",
                content_type="application/pdf",
                filename="nota.pdf",
            )
            assert False, "debería haber levantado DescriptionSkip"
        except DescriptionSkip:
            pass

    # Sin llamada al modelo: el PDF escaneado/textless se omite.
    assert seen == []


async def test_describe_document_bytes_non_pdf_skips_without_llm():
    seen = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(_chat_handler(seen)))
    async with http:
        try:
            await describe_document_bytes(
                b"plain text not pdf", http=http, api_key="sk",
                model="google/gemini-2.5-flash", prompt="x",
                content_type="text/plain", filename="nota.txt",
            )
            assert False, "debería haber levantado DescriptionSkip"
        except DescriptionSkip as exc:
            assert "no PDF" in str(exc)

    # Sin llamada al modelo: el documento no PDF se omite.
    assert seen == []


async def test_describe_document_query_string_url_skips_textless_pdf(monkeypatch):
    monkeypatch.setattr(describe, "_try_extract_pdf_text", lambda raw: "")
    seen = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(_chat_handler(seen)))
    async with http:
        try:
            await describe_document(
                "https://hub.example.com/files/presupuesto.pdf?X-Goog-Signature=abc",
                http=http, api_key="sk",
                model="google/gemini-2.5-flash",
                prompt="resumí",
            )
            assert False, "debería haber levantado DescriptionSkip"
        except DescriptionSkip:
            pass

    # Sólo se descarga el PDF; no debe llegarse al chat de OpenRouter.
    assert [r.url.path for r in seen] == ["/files/presupuesto.pdf"]


async def test_describe_document_url_skips_non_pdf():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200, content=b"plain text", headers={"content-type": "text/plain"},
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with http:
        try:
            await describe_document(
                "https://hub.example.com/files/nota.txt",
                http=http, api_key="sk",
                model="google/gemini-2.5-flash",
                prompt="resumí",
            )
            assert False, "debería haber levantado DescriptionSkip"
        except DescriptionSkip:
            pass

    # Sólo la descarga; no debe llegarse al chat de OpenRouter.
    assert [r.url.path for r in seen] == ["/files/nota.txt"]


async def test_describe_document_forces_flash_model_ignoring_requested(monkeypatch):
    monkeypatch.setattr(describe, "_try_extract_pdf_text", lambda raw: "Texto del PDF. " * 50)
    seen = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(_chat_handler(seen)))
    async with http:
        result = await describe_document_bytes(
            PDF_BYTES,
            http=http, api_key="sk",
            model="openai/gpt-4o",
            prompt="resumí",
            content_type="application/pdf",
            filename="nota.pdf",
        )

    assert result.model == "google/gemini-2.5-flash"
    body = json.loads(seen[0].content)
    assert body["model"] == "google/gemini-2.5-flash"


async def test_describe_document_uses_fixed_summary_instruction(monkeypatch):
    monkeypatch.setattr(describe, "_try_extract_pdf_text", lambda raw: "Texto del PDF. " * 50)
    seen = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(_chat_handler(seen)))
    async with http:
        await describe_document_bytes(
            PDF_BYTES,
            http=http, api_key="sk",
            model="google/gemini-2.5-flash",
            prompt="INSTRUCCION-EDITABLE-IGNORAR",
            content_type="application/pdf",
            filename="nota.pdf",
        )

    content = json.loads(seen[0].content)["messages"][0]["content"]
    assert all(part["type"] == "text" for part in content)
    sent = content[0]["text"]
    assert "INSTRUCCION-EDITABLE-IGNORAR" not in sent
    assert describe._PDF_SUMMARY_INSTRUCTION in sent
    assert "3 o 4 líneas" in sent
    assert "Sin encabezados ni bullets" in sent


async def test_describe_document_bounds_extracted_text(monkeypatch):
    long_text = "A" * 10_000 + "Z" * 100
    monkeypatch.setattr(describe, "_try_extract_pdf_text", lambda raw: long_text)
    seen = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(_chat_handler(seen)))
    async with http:
        await describe_document_bytes(
            PDF_BYTES,
            http=http, api_key="sk",
            model="google/gemini-2.5-flash",
            prompt="resumí",
            content_type="application/pdf",
            filename="nota.pdf",
        )

    sent = json.loads(seen[0].content)["messages"][0]["content"][0]["text"]
    prefix = describe._PDF_SUMMARY_INSTRUCTION + "\n\nDocumento:\n"
    assert sent.startswith(prefix)
    doc = sent[len(prefix):]
    assert doc.startswith("A" * describe._PDF_TEXT_HEAD_CHARS)
    assert doc.endswith("Z" * 100)
    assert "caracteres omitidos" in doc
    assert len(doc) <= describe._PDF_TEXT_MAX_CHARS + 64