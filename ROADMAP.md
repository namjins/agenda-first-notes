# Roadmap

## Version 1 (this release) — scope

- Two isolated workspaces (Ward Council, Bishopric): separate SQLite DBs, files, history, search, prompts.
- Agenda ingestion: screenshot paste/drop (local vision model or Tesseract), text, manual tree editing; immutable approved revisions.
- Transcript paste/upload, versioned corrections, overlapping segmentation with coverage tracking.
- Evidence-first extraction via local Ollama with schema-constrained JSON; quote validation with offsets; owner and over-confidence checks.
- Adversarial review to convergence (configurable rounds, optional second model); *Review Required* when it does not converge.
- Deterministic Markdown renderer; copy and `.md`/`.txt` export.
- Human review UI with evidence highlighting, issues-only mode, manual edits preserved and labelled.
- Historical reconciliation as proposals: follow-up inbox with status history, decisions, calling events (independent states), scheduled-event date changes, append-only audit log.
- FTS5 search per workspace. Reference documents (TXT/MD/PDF/DOCX) with advisory glossary suggestions.
- Localhost-only security, token, repo audit, CI on three OSes, Playwright e2e.

## Version 1.1

- Glossary workflow: accept/reject suggested spellings per note; terminology lists per workspace.
- Encrypted local backup/restore (user passphrase) and import.
- Historical timeline visualization per follow-up / calling.
- More search filters (owner, agenda topic facets) and saved searches.
- Side-by-side comparison of two models' drafts for the same meeting.
- Additional exports (DOCX, PDF) rendered locally.
- Streaming progress per segment and token-level cancellation.

## Version 2

- Local audio transcription (whisper.cpp or similar, portable).
- Additional runtime adapters behind `ModelProvider`: llama.cpp server, MLX (optional, macOS-only acceleration with portable fallback).
- Optional local semantic search (embeddings in SQLite).
- Packaged desktop installers (bundled Python + UI).
- Additional workspace templates beyond the two built-ins (schema is already workspace-agnostic; the id set is closed on purpose in v1).

## Known limitations (v1)

- Review convergence relies on a lexical over-confidence heuristic plus a model reviewer; neither is a guarantee. Human approval is mandatory and the UI enforces it.
- Case B detection (open follow-up listed on the agenda but not discussed) is lexical matching; the user confirms carry-forward.
- `ScheduledEvent` dates extracted from transcripts are stored as the note text with `event_date` unset; the user records the date explicitly.
- Calling events extracted from transcripts do not attempt to parse the person's name (conservative by design); the user can fill it in.
- Cancellation is checked between segments, not mid-generation.
- One global job worker: workspaces process sequentially.
- No encryption at rest; rely on OS disk encryption.
- Screenshot OCR quality depends on the chosen vision model / Tesseract; always review the extracted tree.
- Windows and Linux are covered by CI configuration and portable code, but the primary manual testing so far has been on macOS (see PROGRESS.md for what was actually run).
