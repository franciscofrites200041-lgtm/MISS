# Allow audio and native-text PDFs only

## Outcome
Only audio and PDFs containing embedded native text reach processing. Images, videos, stickers, non-PDF documents, and textless/scanned PDFs are silently skipped without LLM, Vision, or transcription calls.

## Scope
- `app/attachments.py`
- `app/describe.py`
- `app/pipeline.py`
- `tests/test_attachments.py`
- `tests/test_describe.py`
- `tests/test_pipeline.py`

## Tasks
- [x] Add failing regression coverage for excluded media and textless/non-PDF documents.
- [x] Gate webhook media intake and document handling without LLM fallback.
- [x] Apply the same policy to the dashboard document-test endpoint.
- [x] Disable the remaining dashboard image-test provider path.
- [x] Run the full suite and inspect the resulting behavior.
- [x] Force the low-cost Flash model for native-PDF summaries.
- [x] Bound extracted text and enforce the concise summary contract.
- [x] Verify cost-control and summary-format regressions.

## Constraints
- PDFs use `pypdf` native-text extraction only.
- No LLM OCR or PDF file fallback.
- Unsupported media must be silent skips, not failed runs.
- Preserve audio and native-text PDF processing.

## Evidence
- RED: focused attachment/pipeline tests failed for image processing, Vision PDF fallback, and non-PDF failure behavior; document tests could not import the new skip signal before implementation.
- GREEN: `python -m pytest tests/test_attachments.py tests/test_describe.py tests/test_pipeline.py -q` passed 33 tests.
- Dashboard endpoint alignment: its direct `describe_document_bytes` path now raises the shared skip signal for excluded input, before any provider call. Its endpoint maps that signal to the existing 400 response path.
- Independent verification passed the full suite (141 tests) but found a remaining dashboard image-test provider path in `app/main.py`.
- Dashboard image path closed: legacy image tools now return HTTP 400 before a file/provider call; fresh stores do not seed image tools.
- Independent verification: `.venv/Scripts/python.exe -m pytest -q` passed 142 tests. The verifier mapped webhook and dashboard ingress paths and confirmed excluded input cannot invoke a provider.
- Non-blocking: dead image helper functions and dashboard image UI copy remain, but no inbound or dashboard action can invoke an image provider path.
- Follow-up authorized: force Flash for native-PDF synthesis, bound native-text input deterministically, and enforce a short 3–4-line summary including payment details when present.
- Native-PDF synthesis now forces `google/gemini-2.5-flash`; an 8,000-character deterministic head/tail cap limits extracted document text, while the prompt asks for exactly 3–4 Spanish lines with payment fields when present.
- Independent verification: `.venv/Scripts/python.exe -m pytest -q` passed 147 tests. The verifier confirmed model overrides are ignored, only text reaches the provider, textless PDFs still skip without provider calls, and the staged `.claude/settings.local.json` is unrelated.
