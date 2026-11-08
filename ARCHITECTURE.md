# Architecture

A modular monolith: a Python/FastAPI backend that owns all state and serves a compiled React UI. Ports-and-adapters only where there is a real seam: the model provider (Ollama today) and image/document ingestion.

```
frontend/  (React + TypeScript + Vite + Tailwind)     ──HTTP /api──▶  backend/agenda_first_notes/
                                                                   ├── api.py          HTTP routes; validates workspace id on every call
                                                                   ├── services.py     the only place state changes (meetings, agenda, transcript, processing, notes, approval)
                                                                   ├── history.py      explicit, audited updates to follow-ups / decisions / callings / event dates
                                                                   ├── pipeline.py     extraction → evidence validation → review round (pure functions)
                                                                   ├── evidence.py     quote location, owner support, overconfidence heuristic
                                                                   ├── agenda.py       agenda tree model, text parsing, outline for the model
                                                                   ├── segmentation.py overlapping segments with offsets + coverage
                                                                   ├── render.py       deterministic Markdown from approved tree + verified notes
                                                                   ├── prompts.py      versioned core policy + workspace profiles
                                                                   ├── llm/            ModelProvider port; ollama_provider.py, fake.py
                                                                   ├── ingest.py       image validation, OCR (vision model / Tesseract), PDF/DOCX text
                                                                   ├── jobs.py         durable single-worker job runner (rows in the workspace DB)
                                                                   ├── search.py       SQLite FTS5 per workspace
                                                                   ├── models.py       SQLAlchemy ORM (one schema, one DB per workspace)
                                                                   ├── workspaces.py   closed set of workspace ids → directories, engines, safe file paths
                                                                   ├── security.py     loopback Host, Origin allowlist, token, no-store
                                                                   ├── config.py       platformdirs data dir, global (non-sensitive) settings
                                                                   ├── repo_audit.py   pre-commit / CI content audit
                                                                   └── cli.py          serve / paths / migrate / audit
```

## Domain boundaries

| Boundary | Lives in | Rule |
|---|---|---|
| UI | `frontend/` | Talks only to `/api`. Holds no transcript in browser storage (only workspace choice and theme). |
| Application services | `services.py`, `history.py` | Every state change. Enforce the meeting state machine and the workspace. |
| Domain | `agenda.py`, `render.py`, `segmentation.py`, `evidence.py`, `pipeline.py` | Pure; no I/O; no history. |
| Model providers | `llm/` | `ModelProvider` protocol: `health()`, `list_models()`, `generate(GenerateRequest) -> str`. Ollama adapter refuses non-loopback hosts. |
| Ingestion | `ingest.py` | Validates bytes with Pillow before anything else touches them. |
| Persistence | `models.py`, `workspaces.py`, `db.py` | One SQLite file per workspace; Alembic migrations run per workspace at startup. |
| Search | `search.py` | FTS5 virtual table inside each workspace DB. |
| Export | `render.py`, `api.export_notes` | Approved revision's stored Markdown, or the live preview. |

## Workspace isolation

`workspaces.WORKSPACES` is a closed dict (`ward_council`, `bishopric`). `get_workspace(id)` validates the id and returns paths derived from it; nothing client-supplied becomes a path. Each workspace has its own SQLAlchemy engine and SQLite file, so a query cannot see another workspace's rows even by accident. Uploads, references and exports are resolved through `Workspace.file_path()`, which flattens filenames to one safe component and refuses symlinks or escapes. Jobs are rows in the workspace DB and the runner re-derives the workspace from the validated id. Model prompts are built in `pipeline.py` from exactly three inputs: the approved agenda tree, the current transcript segment, and the workspace profile string.

## Agenda representation

```json
{"id": "n3f2a9c10", "text": "Relief Society", "kind": "group", "children": [ ... ]}
```

Ids are random and stable through edits (the editor preserves them; new nodes get fresh ids). `AgendaRevision` rows are immutable once `approved_at` is set; any edit creates revision n+1 that must be approved again before processing. The model only ever sees `- [id] text` outlines and must return node ids; unknown ids become "unmapped" findings.

## Evidence model

A `Note` has `evidence: [{start, end, quote, transcript_version_id}]`. `evidence.validate_quotes` locates each model-cited quote in the segment (exact, then whitespace-tolerant) and stores global offsets into the original transcript. A note with zero located quotes is `unsupported` and never rendered. `owner_supported` requires every token of the owner's name to appear in the cited quotes, otherwise the owner is stripped (finding `inferred_owner`). `looks_overconfident` is a lexical heuristic (hedged evidence + confident text) that flags for the model reviewer and blocks convergence.

When a transcript correction creates a new `TranscriptVersion`, `evidence.revalidate` re-locates every note's quotes; anything that moved is updated, anything that vanished marks the note `stale` + `needs_review`.

Statuses: `verified`, `uncertain` (model said high uncertainty), `needs_review`, `unsupported`, `user_edited` (user text whose evidence/owner still check out), `manual_unverified` (user text without evidence). User-origin notes are never changed by the reviewer.

## Processing pipeline (`services.process_meeting`)

1. Validate: transcript exists, latest agenda revision is approved, model selected. State → `processing`. Previous model-origin notes and findings are deleted (user notes kept) — re-running is idempotent.
2. `segmentation.segment()` splits at paragraph/line/sentence boundaries with overlap; each `Segment` tracks `status`. Coverage is persisted after every segment.
3. Per segment: `pipeline.extract_segment` → model JSON (schema-constrained) → evidence validation → deterministic findings. A failed segment is recorded, not hidden.
4. `pipeline.dedupe` merges equivalent claims from overlapping segments.
5. `services.run_review`: up to `max_review_rounds` of `pipeline.review_round` (model) + `pipeline.deterministic_checks`. Actions on model notes: remove / rewrite / move / strip_owner / clear_follow_up / add (adds require located evidence). Converged ⇔ no material (high) findings remain in a round.
6. State → `ready_for_approval` if converged and coverage complete, else `review_required`.
7. `render.render` builds Markdown from the approved tree + non-unsupported notes; placeholders depend on coverage (`Not discussed.` only when all segments succeeded).

## Historical reconciliation (`services._propose_history`)

On approval, an immutable `NotesRevision` snapshot is written, then for each live note: `follow_up` → `FollowUp(status=proposed)` + `FollowUpEvent`; `decision`/`proposed_decision` → `Decision(confirmed|proposed|unverified)`; `calling_event` → `CallingEvent`; `date` → `ScheduledEvent(event_date=None)`. Each carries `origin` (transcript|user) and the evidence, and writes an `AuditEvent`. Nothing existing is modified. Users then confirm/edit in the inbox (`history.py`), each change appending `FollowUpEvent`/`AuditEvent` rows; date changes create a superseding `ScheduledEvent` instead of overwriting.

Undiscussed agenda follow-ups (Case B) are detected lexically by `services.undiscussed_agenda_follow_ups`, shown in the review panel, and only enter the final follow-up list when the user ticks them (`Meeting.carry_forward`). Historical items absent from the agenda (Case C) never touch the notes.

## Rendering

`render.render(title, meeting_date, tree, notes, coverage_complete, extra_follow_ups)` is pure and tested against golden strings. Rules: exact node text, two-space nesting, notes as child bullets, `[Follow-up]` then `[Owner: …]` at the end, one placeholder per undiscussed leaf or per fully-undiscussed group (children listed bare), then exactly one `Next Meeting – Follow-Up Items` flat list (deduped, carry-forwards appended, or `No follow-up items identified.`).

## Meeting state machine (`models.TRANSITIONS`)

`draft → agenda_imported → agenda_verified → transcript_added → processing → {ready_for_approval | review_required | failed} → approved → archived`, with explicit back-edges for edits (`agenda_imported` on agenda change, `transcript_added` on transcript change or cancel, `ready_for_approval` on reopen). `services.transition()` rejects anything else. Approval from `review_required` is allowed only once no unresolved high-severity findings remain.

## Database schema (per workspace)

`meetings`, `agenda_revisions`, `agenda_images`, `transcript_versions`, `notes`, `notes_revisions`, `review_findings`, `jobs`, `follow_ups`, `follow_up_events`, `decisions`, `calling_events`, `scheduled_events`, `audit_events` (append-only), `reference_documents`, and the FTS5 virtual table `search_index(entity_type, entity_id, meeting_id, meeting_date, status, title, body)`. Column details: `backend/agenda_first_notes/models.py`. Migrations: `backend/alembic/versions/`.

## Jobs

`jobs.py` runs a single worker thread (models run sequentially to conserve memory). A job is a row with `status/progress/message/cancel_requested`; the processing loop checks `cancel_requested` between segments. `enqueue` refuses a second active job for the same meeting and kind. At startup, jobs left `queued/running` are marked failed with a retry hint.
