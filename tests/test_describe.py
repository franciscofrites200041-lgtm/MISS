import base64
import json

import httpx

from app import describe
from app.describe import (
    DescriptionError,
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


async def test_describe_document_bytes_scanned_pdf_uses_file(monkeypatch):
    monkeypatch.setattr(describe, "_try_extract_pdf_text", lambda raw: "")
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

    assert result.latency_ms is not None
    body = json.loads(seen[0].content)
    content = body["messages"][0]["content"]
    file_part = next(p for p in content if p["type"] == "file")
    assert file_part["file"]["filename"] == "nota.pdf"
    assert file_part["file"]["file_data"].startswith("data:application/pdf;base64,")


async def test_describe_document_bytes_rejects_non_pdf_without_text():
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(500))
    )
    async with http:
        try:
            await describe_document_bytes(
                b"plain text not pdf", http=http, api_key="sk",
                model="google/gemini-2.5-flash", prompt="x",
                content_type="text/plain", filename="nota.txt",
            )
            assert False, "debería haber levantado DescriptionError"
        except DescriptionError as exc:
            assert "no soportado" in str(exc)


async def test_describe_document_query_string_url_detects_pdf_from_filename(monkeypatch):
    monkeypatch.setattr(describe, "_try_extract_pdf_text", lambda raw: "")
    seen = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(_chat_handler(seen)))
    async with http:
        result = await describe_document(
            "https://hub.example.com/files/presupuesto.pdf?X-Goog-Signature=abc",
            http=http, api_key="sk",
            model="google/gemini-2.5-flash",
            prompt="resumí",
        )
    assert result.latency_ms is not None
    assert seen[0].url.path == "/files/presupuesto.pdf"
    body = json.loads(seen[1].content)
    content = body["messages"][0]["content"]
    file_part = next(p for p in content if p["type"] == "file")
    assert file_part["file"]["filename"] == "presupuesto.pdf"