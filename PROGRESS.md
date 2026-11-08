# Progress

Hand-off record for continuing work across sessions. Update when a milestone or a verification status changes.

## Status by milestone

| Milestone | Status | Notes |
|---|---|---|
| 1 Foundation | Done | uv/pyproject, FastAPI app, platformdirs data dir, two workspace DBs, Alembic migration 0001, security middleware, pytest, CI matrix, pre-commit, repo audit. |
| 2 Vertical slice | Done | Workspace select → meeting → agenda (text/tree/screenshot) → approve → transcript → generate (Ollama port) → deterministic Markdown → copy/export. E2E Playwright test covers it. |
| 3 Evidence & review | Done | Segmentation with offsets/coverage, quote validation, owner/over-confidence checks, model review rounds with actions, user edits preserved and labelled, approval with immutable revision, transcript corrections → stale notes. |
| 4 Historical intelligence | Done | Follow-up inbox (statuses, events, Prepare Next Agenda), decisions, calling events (independent, admin entry), scheduled-event date supersession, append-only audit, FTS5 search, Case A/B/C handling. |
| 5 Usability | Done (v1 level) | Autosave (debounced transcript, title/date, agenda), resume, job progress/cancel/retry, model settings + synthetic test inference, shortcuts (1–7, w, g, a, c, ?), light/dark, error boxes. |
| 6 Hardening | In progress | Docs written. Real-model run with qwen3.6:35b-a3b exercised the pipeline; found and fixed: reasoning models returning empty content (now `think=False`), optional schema fields omitted (now all-required schema), failed segments invisible (now a high finding). Final adversarial reviews A–G: see below. |

## What was actually verified (macOS, M2 Ultra, 2026-11-08)

- `uv run pytest -q`: 26 passed, 1 skipped (real-model test skipped without `AFN_REAL_MODEL`).
- `ruff check`, `ruff format --check`, `mypy`: clean.
- `npm run typecheck`, `npm test` (4 unit tests), `npm run build`: clean.
- `npm run e2e`: 3 Playwright tests passed in Chromium against the real backend (`AFN_PROVIDER=fake`): token gate, full workflow incl. agenda edit/approve, transcript autosave, generation, evidence highlight, manual edit labelling, approval, clipboard copy, history, workspace isolation in UI/search, dark mode, settings.
- `agenda-first-notes audit`: passed.
- Real local model (`qwen3.6:35b-a3b`) via `tests/test_real_model.py`: see "Real model results" below.

Not verified here: Windows and Linux runs (CI config exists; it has not been executed because the repo has not been pushed). Tesseract OCR path and vision-model OCR were implemented but not exercised with a real screenshot in this session.

## Real model results

- Run 1: extraction returned empty content after 44 s → segment failed → correctly rendered "processing incomplete" placeholders and `review_required`, but the cause was invisible. Fixed (see Milestone 6).
- Run 2: pending/see latest log in session; record outcome here.

## Remaining / next steps

1. Record run-2 real-model outcome; try `qwen3.5:27b` and `gpt-oss:20b` once each and note behavior.
2. Exercise screenshot OCR with a synthetic agenda image (generate one with Pillow) through the vision model and Tesseract.
3. Push to GitHub (needs maintainer approval) and confirm CI on Windows/Linux.
4. Add screenshots (synthetic data) to README.
5. Adversarial implementation reviews A–G and fix material findings; record outcomes here.
