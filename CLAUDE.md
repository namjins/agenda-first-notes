# CLAUDE.md — working agreement for AI-assisted development

Read this before changing anything. Keep it current.

## What this is

Agenda First Notes: a local-first (FastAPI + React) app that turns agendas + transcripts into evidence-grounded Markdown meeting notes for two isolated workspaces (Ward Council, Bishopric), with historical follow-up/decision/calling tracking. Everything runs locally through Ollama. See ARCHITECTURE.md.

## Authoritative note-generation policy (do not weaken without explicit approval)

1. **Agenda authority.** The approved agenda tree is the structure. Exact text, order, nesting. Never rename, merge, remove, reorder, flatten, or invent headings. Undiscussed leaf → `Not discussed.`; wholly undiscussed group → one `No discussion.` at group level, children listed bare; never a placeholder on a parent whose descendants were discussed. If transcript coverage is incomplete, never claim absence. An agenda item is not evidence that its action occurred.
2. **No agenda** → propose headings only from substantive current-transcript discussion; user approves; then fixed.
3. **Current transcript is the only factual authority.** History, reference docs and the agenda never supply facts (names, owners, decisions, dates, calling states, completions). Glossaries only *suggest* spellings for confirmation.
4. **Outcomes, not conversation.** No chronology, no filler, no timestamps.
5. **Unclear speakers:** meaning over attribution; never guess who said what; no invented consensus. Fallback text: `Discussion occurred; no clear decisions or action items identified.`
6. **Preserve uncertainty.** Question ≠ decision, suggestion ≠ assignment, proposal ≠ completion. Downgrade wording to what the evidence supports.
7. **Names:** full names only with current-meeting evidence; never from role, history or inference.
8. **Callings:** suggested / under_consideration / approved_to_proceed / extended / accepted / declined / sustained / set_apart / released / not_confirmed are independent events; never auto-advance.
9. **Tags:** `[Follow-up]` only when action is genuinely needed; `[Owner: Full Name]` only when explicitly assigned/accepted and named in the evidence; tags at the end of the bullet.
10. **Final section:** exactly one `Next Meeting – Follow-Up Items`, flat, deduped, no completed items, no automatic historical carry-over; `No follow-up items identified.` when empty.
11. **Formatting:** plain Markdown bullets, two-space nesting, no bold headings, no tables, no people lists, no extra sections, human-readable date only when confirmed by the user.
12. **Core rule: when uncertain, prefer omission or explicit uncertainty over inference.**

Encoded in `backend/agenda_first_notes/prompts.py` (`PROMPT_VERSION`), enforced by `evidence.py`, `pipeline.py`, `render.py`; tested in `tests/test_agenda_render.py` and `tests/test_workflow.py`. Workspace profiles may add emphasis, never relax a rule.

## Source-of-truth hierarchy

Current approved agenda (structure) > current transcript, latest approved version (facts) > explicit user edits (labelled `user_edited` / `manual_unverified`, never `verified`) > history (separate UI, proposals only) > reference documents (advisory only).

## Three follow-up cases (mandatory distinction)

A. Transcript establishes an unresolved action → in notes and final list.
B. Agenda lists an existing action, not discussed → agenda item preserved, `Not discussed.`, inbox status unchanged, user may tick carry-forward (`Meeting.carry_forward`).
C. Historical action absent from agenda and transcript → stays in the inbox only.

## Workspace boundaries

Closed id set in `workspaces.py`. One SQLite DB + directory per workspace. Every service takes a `Workspace`; every route validates the id; paths come only from the id. No cross-workspace query, search, prompt context, cache, job or export. Tests with identical content in both workspaces must keep passing.

One exception (maintainer decision, 2026-11-08): the member list is shared by both workspaces, stored once in `<data>/members.sqlite3` (`workspaces.members_session`). Meeting data, name confirmations, prompts, search, history and jobs stay isolated per workspace; prompts get only that meeting's confirmed heard→name pairs.

## Privacy rules

- Local inference only (`OllamaProvider` refuses non-loopback). No cloud, no fallback, no telemetry, no CDN, no automatic backups.
- Runtime data outside the repo (`config.assert_outside_repo`). Never commit real data; fixtures carry `SYNTHETIC`.
- Never put real agendas, transcripts, names or member data into tests, fixtures, logs, screenshots, docs, memory files or commits. Exception (maintainer decision, 2026-11-08): the maintainer may share real samples with the coding assistant in chat for development. Keep them only outside the repo (scratchpad or a folder the maintainer names), never copy them into the repo, and turn what they teach into synthetic fixtures with invented names.
- The model has no tools; all state changes pass through validated services.
- Member, ward and calling lists (rosters, glossaries) are used only inside the app and the local model. Never commit them, push them to GitHub, send them to web searches or any online service, or paste them into AI-assistant chats. Synthetic stand-ins only in tests.
- Do not claim encryption at rest or church affiliation/approval.

## Architecture conventions

- Layers: `api.py` → `services.py`/`history.py` → `models.py`; pure domain modules never import persistence.
- The model returns JSON bound to agenda node ids; rendering is deterministic from the tree.
- Approved artifacts (agenda revisions, notes revisions) are immutable; changes create new revisions. Historical changes append `AuditEvent`s; dates are superseded, not overwritten.
- Meeting state transitions only via `services.transition()`.
- Jobs: `jobs.py` single worker; cancellation between segments; idempotent re-processing.
- Keep diffs small; reuse what exists; mark deliberate limits with `shortcut:` comments.

## Commands

```sh
cd backend && uv sync --all-extras
uv run pytest -q                       # 240 tests (+1 real-model, skipped), synthetic data, fake model
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run agenda-first-notes serve|paths|migrate|audit [--staged]
AFN_REAL_MODEL=<model> uv run pytest tests/test_real_model.py -s   # optional, needs local Ollama

cd frontend && npm ci
npm run typecheck && npm test && npm run build
npm run e2e                            # Playwright; starts backend with AFN_PROVIDER=fake

python3 run.py                         # user launcher
```

## Repository safety

Pre-commit: repo audit (staged), ruff, gitleaks, private-key and large-file checks. CI: three OSes, tests, e2e, gitleaks, pip-audit, npm audit. Do not push or publish without the maintainer's explicit approval.

## Progress

`PROGRESS.md` is the hand-off file between sessions: what is done, what was actually verified, what remains.
