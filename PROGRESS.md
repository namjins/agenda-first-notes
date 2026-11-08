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

Also verified: Tesseract and vision-model (qwen3.6) OCR on a Pillow-generated synthetic agenda screenshot, nesting preserved. Not verified here: Windows and Linux runs (CI config exists; not executed because the repo has not been pushed).

## Real model results (ward council fixture, 1 segment, 1 review round, M2 Ultra)

| Model | Outcome | Time | Notes |
|---|---|---|---|
| qwen3.6:35b-a3b | ready_for_approval, 8 notes | ~30 s | Correct placement, hedged wording, `[Owner: Rowan Castellano]` and `[Owner: Pat]` kept with evidence. Early runs exposed three bugs, all fixed: empty content from reasoning models (→ `think=False`), optional schema fields omitted (→ all-required schema), reviewer "contamination" hallucinations about names present in verified evidence (→ deterministic rejection). |
| qwen3.5:27b | ready_for_approval, 8 notes | ~116 s | Converged; reviewer emitted only low-severity confirmations. |
| gpt-oss:20b | ready_for_approval, 8 notes | ~72 s (8k output tokens) | Ignores/breaks on `think=False` (→ plain-call fallback). Misquoted one passage ("Speaker 1: Rowan Castellano…"), so the quote was rejected and the owner stripped — conservative behavior as designed. |

Invariants asserted in every run: all stored evidence offsets match the original transcript verbatim; every `[Owner]` name appears in its cited evidence; headings verbatim; exactly one follow-up section; full coverage.

## Review A — acceptance criteria (Section 29) as of this session

| # | Criterion | Status | How verified |
|---|---|---|---|
| 1 | Install on a supported OS | macOS ✔; Win/Linux ◐ | `python run.py` path exercised on macOS; CI matrix written, not yet run |
| 2 | Launch locally | ✔ | `agenda-first-notes serve` (Playwright webServer + manual) |
| 3 | Connect to a local Ollama model | ✔ | Settings page lists models; real-model tests |
| 4 | Choose Ward Council or Bishopric | ✔ | e2e workspace selector |
| 5 | Create a meeting | ✔ | e2e |
| 6 | Upload or paste an agenda screenshot | ✔ | API upload test (rejects non-images); Tesseract and vision-model OCR on a generated synthetic PNG, nesting preserved; UI paste handler implemented (not browser-automated) |
| 7 | Review and correct the extracted agenda | ✔ | tree editor unit tests + e2e edit |
| 8 | Approve the agenda | ✔ | e2e; immutability test |
| 9 | Paste a transcript | ✔ | e2e autosave |
| 10 | Generate structured notes | ✔ | e2e (fake) + 3 real models |
| 11 | View evidence for material claims | ✔ | e2e highlight assertion |
| 12 | Run adversarial review | ✔ | automatic after extraction; real-model runs show rewrites/adds/rejections |
| 13 | See and correct unresolved issues | ✔ | issues-only mode, dismiss/resolve, edit → labelled |
| 14 | Approve final notes | ✔ | e2e + API tests (blocked by open high findings / missing date) |
| 15 | Copy clean Markdown | ✔ | e2e clipboard read |
| 16 | Export notes | ✔ | API md/txt tests; UI buttons |
| 17 | Reopen an earlier meeting | ✔ | dashboard resume + history links; reopen endpoint test |
| 18 | Search meeting history | ✔ | FTS tests + e2e |
| 19 | View unresolved follow-ups | ✔ | inbox API tests + screenshot |
| 20 | Update a task explicitly | ✔ | inbox API tests (status history, audit) |
| 21 | View historical changes | ✔ | audit tab, event date supersession test |
| 22 | Switch workspaces without contamination | ✔ | isolation test with identical content; e2e |
| 23 | Restart without losing saved data | ✔ | SQLite on disk; jobs recovery at startup (unit-level) — manual restart not scripted |
| 24 | Verify user data is outside the repository | ✔ | `assert_outside_repo` test; `paths` command; audit |

Demonstrations (Section 29 list): ward council with agenda ✔ (fixture), bishopric with agenda ✔ (isolation test), bishopric without agenda ✔ (propose + calling states test), nested agenda ✔, undiscussed item ✔, overlapping speakers ✔ (fixture crosstalk → "unclear who"), ambiguous name ✔ (owner stripped), proposed-but-unapproved calling ✔ (`suggested` only), explicit assignment ✔, unresolved question ✔, historical follow-up absent from transcript ✔ (Case B/C test), event date changed ✔, unsupported historical fact excluded ✔ (prompt-content assertions), long transcript without loss ✔ (segment test), flat follow-up list ✔, Markdown indentation ✔ (golden test).

## Remaining / next steps

1. Push to GitHub (needs maintainer approval) and confirm CI on Windows/Linux.
2. Adversarial implementation reviews A–G and fix material findings; record outcomes here.
