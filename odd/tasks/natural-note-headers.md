# Render natural media-note headers

## Outcome
CRM notes use one clear, human-readable heading instead of stacked bracket labels, followed by the generated summary on a separate line.

## Scope
- `app/pipeline.py`
- `tests/test_pipeline.py`
- `tests/test_e2e.py`
- `app/tools_store.py`
- `tests/test_tool_testing.py`
- `dashboard/lib/types.ts`
- `dashboard/app/tools/[slug]/editor.tsx`
- `docker-compose.yml`
- `.env.example`

## Tasks
- [x] Add failing examples for natural own and inbound media headers.
- [x] Compose a single media-aware sender heading without changing the summary prompt.
- [x] Add a persistent per-instance operator-directory snapshot with a one-week refresh interval.
- [x] Render own-media headers with the cached operator name and safely fall back to the numeric ID.
- [x] Verify live, cached, stale, malformed, and missing-operator behavior.
- [x] Verify audio/document and own/inbound provenance behavior end to end.
- [x] Remove the obsolete dashboard/configuration note-prefix control without breaking existing SQLite databases.

## Constraints
- Own media renders `Documento enviado por Operador <id>` or `Audio enviado por Operador <id>`.
- Inbound media renders a corresponding received-from-contact heading.
- The generated summary begins after a blank line.
- Do not include technical bracket labels such as `[Operador: 10]`, `[Resumen de documento]`, or `[Transcripción de audio]` in the emitted CRM note.
- Keep `id_user`, `id_orig_quote`, and source-provenance behavior unchanged.
- Do not mutate existing untracked `.claude/` or `.codegraph/` content.
- Existing SQLite databases must remain readable during removal of the obsolete `note_prefix` column.
- Operators endpoint response is `{"data": [{"id": ..., "nombre": ..., "email": ..., "telefono": ..., "fec_ult_conexion": ...}]}`.
- Resolve the endpoint from the webhook `mass_url` as `/hynts/getdata.json?entity=operadores&instance=<root instance>`.
- Persist an operator directory per root instance and refresh it at most once per week; retain the last valid snapshot on endpoint failure.

## Evidence
- RED: `python -m pytest tests/test_pipeline.py tests/test_e2e.py -q` failed 14 updated/new presentation assertions before implementation.
- GREEN: focused suite passed 29 tests; independent full suite passed 176 tests.
- User authorized removal of the obsolete dashboard/configuration note-prefix control after review discovered it no longer affects emitted notes.
- Legacy SQLite compatibility: rows retaining the retired column remain readable through explicit column selection and row projection; no destructive migration runs.
- Final independent verification: `.venv/Scripts/python.exe -m pytest -q` passed 179 tests and `dashboard/npm run build` passed with no TypeScript errors.
- Native reliability review approved and acknowledged for the earlier header candidate: `review-66a6fc522032a169`.
- Operator-directory focused suite passed 34 tests; complete suite passed 184 tests (two pre-existing dependency deprecation warnings).
- Commit pending explicit user authorization.
