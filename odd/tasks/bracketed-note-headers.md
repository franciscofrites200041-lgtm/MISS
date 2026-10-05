# Bracket media note headers

## Outcome
CRM provenance headers are visually distinct from generated summaries for every audio and document note.

## Scope
- `app/pipeline.py`
- `tests/test_pipeline.py`

## Tasks
- [x] Add failing assertions for bracketed own and inbound headings.
- [x] Wrap every generated media-note heading in brackets while keeping the blank-line separator.
- [x] Run focused and full regression tests.

## Constraints
- Preserve existing own/inbound wording and operator/contact attribution.
- Keep the generated summary body unchanged after one blank line.
- Do not modify untracked `.claude/` or `.codegraph/` content.
- Do not commit or push without explicit user authorization.

## Evidence
- RED: `python -m pytest tests/test_pipeline.py -q` failed the new bracketed-header assertion before implementation.
- GREEN: focused pipeline and end-to-end tests passed 31 tests; full suite passed 185 tests with two dependency deprecation warnings.
- Native reliability review approved and acknowledged: `review-435617985c4914ca`.
- Non-blocking review notes to consider later: a contact or operator name containing a literal `]` is not sanitized before bracketing; the private `_note_header` assertion in `tests/test_pipeline.py:89` is a unit-level suggestion.
- Commit pending explicit user authorization.
