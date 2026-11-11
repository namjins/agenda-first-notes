# Contributing

## Developer setup

```sh
# backend
cd backend
uv sync --all-extras
uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run agenda-first-notes serve --no-browser --reload     # API on :8765

# frontend (separate terminal)
cd frontend
npm ci
npm run dev                                              # UI on http://127.0.0.1:5173 (proxies /api)
```

In dev the Vite origin is a loopback origin, so it is accepted automatically. Open `http://127.0.0.1:5173/#token=<token>` where the token is in `agenda-first-notes paths`' data directory (`api_token`), or just run the production build (`npm run build`) and use `python run.py`.

Install the pre-commit hooks once: `uvx pre-commit install`.

## Test commands

| What | Command |
|---|---|
| Backend unit + API tests (mocked model) | `cd backend && uv run pytest -q` |
| Real local model smoke test (optional) | `AFN_REAL_MODEL=qwen3.8:27b uv run pytest tests/test_real_model.py -s` |
| Frontend unit tests | `cd frontend && npm test` |
| Type-check UI | `npm run typecheck` |
| End-to-end in a real browser | `npm run e2e` (starts the backend with `AFN_PROVIDER=fake`) |
| Repository audit | `cd backend && uv run agenda-first-notes audit` |

CI runs all of these on macOS, Windows and Linux (`.github/workflows/ci.yml`).

## Coding standards

- Python 3.12+, Ruff (lint + format, line length 120), mypy. TypeScript strict.
- Keep the layering: UI → `api.py` → `services.py`/`history.py` → models. Domain modules (`agenda`, `render`, `segmentation`, `evidence`, `pipeline`) stay pure and never import persistence.
- Every state change goes through a service and, for historical records, writes an `AuditEvent`.
- Never pass historical records into prompt construction. Never let the model control hierarchy.
- Shortcuts with a known limit carry a `shortcut:` comment naming the limit and the upgrade path.
- Small, focused PRs. Add or extend a test for behavior you change; golden Markdown strings live in `tests/test_agenda_render.py`.

## The note-generation policy is load-bearing

`CLAUDE.md` and `backend/agenda_first_notes/prompts.py` encode the authoritative policy (agenda fidelity, evidence-first, uncertainty over inference, flat follow-up list, no historical contamination). Changes that weaken it need explicit maintainer approval and a `PROMPT_VERSION` bump.

## Synthetic fixtures only

Everything under `backend/tests/fixtures/`, `frontend/e2e/`, docs and screenshots must be fictional. Text fixtures must contain the word `SYNTHETIC`; the audit enforces it. Never paste real agendas, transcripts, names or member data into code, tests, issues, commit messages or AI-assistant prompts.

## Cross-platform

No Bash/PowerShell in the critical path — launchers and tooling are Python or npm scripts. Use `pathlib`, `platformdirs`, and avoid OS-specific OCR/clipboard libraries. If you add a platform-specific accelerator, keep a portable default.

## Pull requests

- Describe the behavior change and how you tested it (paste the test command output summary).
- CI must be green on all three OSes.
- No new runtime dependencies without a sentence on why stdlib/platform/existing deps don't cover it.
