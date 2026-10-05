# Attribute media notes to their sender

## Outcome
Every audio or document note preserves its original Spoter quote identifier and is attributed to the correct sender: the conversation contact for inbound media and the emitting operator for outbound media.

## Scope
- `app/models.py`
- `app/attachments.py`
- `app/pipeline.py`
- `app/actions.py`
- `tests/test_models.py`
- `tests/test_attachments.py`
- `tests/test_actions.py`
- `tests/test_pipeline.py`
- `tests/test_e2e.py`

## Tasks
- [x] Add failing contract tests for `message.id_original`, `message.operacion`, and `agregar_nota.id_orig_quote`.
- [x] Carry sender provenance through attachment extraction and action construction.
- [x] Select the emitting operator as `id_user` for own media, retain the service user otherwise, and verify the full webhook-to-action behavior.

## Constraints
- `agregar_nota.id_orig_quote` receives `message.id_original`, never `message.id`.
- `message.operacion` is used only for own (`propio: true`) media.
- Inbound media retains `MISS_SERVICE_USER_ID` as `id_user`; its note content identifies the contact/customer.
- Missing or malformed optional provenance must preserve safe existing behavior and must not block media processing.
- Do not mutate existing untracked `.claude/` or `.codegraph/` content.

## Evidence
- RED: `.venv/Scripts/python.exe -m pytest tests/test_models.py tests/test_attachments.py tests/test_actions.py tests/test_pipeline.py tests/test_e2e.py -q` failed 16 new or changed provenance assertions before implementation.
- GREEN: the same focused suite passed 60 tests after implementation.
- Independent verification: `.venv/Scripts/python.exe -m pytest -q` passed 161 tests; the verifier confirmed `message.id` never enters the quote-ID chain and that inbound media cannot select `message.operacion` as `id_user`.
- Native reliability review approved and acknowledged for the implementation candidate: `review-3daaf6b5e1f74d20`.
- Commit pending explicit user authorization.
