# Parse Spoter operator objects for note attribution

## Outcome
Own audio and document notes use the emitting Spoter operator (`message.operacion.id_user`) as `id_user` and visibly identify that operator, while original quote linking remains intact.

## Scope
- `app/models.py`
- `tests/test_models.py`
- `tests/test_attachments.py`
- `tests/test_pipeline.py`
- `tests/test_e2e.py`

## Tasks
- [x] Add failing contract coverage for object-shaped `message.operacion.id_user`.
- [x] Normalize valid operator objects without accepting malformed metadata as an operator ID.
- [x] Verify propagation through attachment, pipeline, action payload, and the full deterministic suite.

## Constraints
- Read `id_user` only from `message.operacion.id_user`; do not infer it from other operation metadata.
- Keep string/integer operator compatibility if existing valid integrations rely on it.
- Missing, empty, boolean, object-without-`id_user`, or malformed `id_user` must use the existing service-user fallback.
- Preserve `message.id_original` to `agregar_nota.id_orig_quote` behavior.
- Do not mutate existing untracked `.claude/` or `.codegraph/` content.

## Evidence
- Production payload exposed that `message.operacion` is an object with `id_user`, not a scalar.
- RED: `.venv/Scripts/python.exe -m pytest tests/test_models.py -q` failed the new object-shape extraction assertions before the parser change.
- GREEN: focused provenance tests passed after extracting only `operacion.id_user`; malformed, missing, nested, empty, boolean, and list forms preserve fallback behavior.
- Independent final verification: `.venv/Scripts/python.exe -m pytest -q` passed 174 tests.
- Native reliability review approved and acknowledged for the implementation candidate: `review-0fe5d18196785d1a`.
- Commit pending explicit user authorization.
