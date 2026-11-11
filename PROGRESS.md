# Progress

Hand-off record for continuing work across sessions. Update when a milestone or a verification status changes.

## Status by milestone

| Milestone | Status | Notes |
|---|---|---|
| 1 Foundation | Done | uv/pyproject, FastAPI app, platformdirs data dir, two workspace DBs, Alembic migration 0001, security middleware, pytest, CI matrix, pre-commit, repo audit. |
| 2 Vertical slice | Done | Workspace select → meeting → agenda (text/tree/screenshot) → approve → transcript → generate (Ollama port) → deterministic Markdown → copy/export. E2E Playwright test covers it. |
| 3 Evidence & review | Done | Segmentation with offsets/coverage, quote validation, owner/over-confidence checks, model review rounds with actions, user edits preserved and labelled, approval with immutable revision, transcript corrections → stale notes. |
| 4 Historical intelligence | Done | Follow-up inbox (statuses, events, Prepare Next Agenda), decisions, calling events (independent, admin entry), scheduled-event date supersession, append-only audit, FTS5 search, Case A/B/C handling. |
| 5 Usability | Done (v1 level) | Autosave (debounced transcript, title/date, agenda), resume, job progress/cancel/retry, model settings + synthetic test inference, shortcuts (1–8, w, g, a, c, ?), light/dark, error boxes. |
| 6 Hardening | Done for v1 | Docs written. Real-model run with qwen3.6:35b-a3b exercised the pipeline; found and fixed: reasoning models returning empty content (now `think=False`), optional schema fields omitted (now all-required schema), failed segments invisible (now a high finding). Adversarial reviews A–G done; all material findings fixed (see below). |

## What was actually verified (macOS, M2 Ultra, 2026-11-08)

- `uv run pytest -q`: 81 passed, 1 skipped (after review H) (real-model test skipped without `AFN_REAL_MODEL`).
- `ruff check`, `ruff format --check`, `mypy`: clean.
- `npm run typecheck`, `npm test` (11 unit tests), `npm run build`: clean.
- `npm run e2e`: 3 Playwright tests passed in Chromium against the real backend (`AFN_PROVIDER=fake`): token gate, full workflow incl. agenda edit/approve, transcript autosave, generation, evidence highlight, manual edit labelling, approval, clipboard copy, history, workspace isolation in UI/search, dark mode, settings.
- `agenda-first-notes audit`: passed.
- Real local model (`qwen3.6:35b-a3b`) via `tests/test_real_model.py`: see "Real model results" below.

Also verified: Tesseract and vision-model (qwen3.6) OCR on a Pillow-generated synthetic agenda screenshot, nesting preserved. Windows and Linux: verified by CI run 42523863717 (all jobs green, 2026-11-08).

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
| 1 | Install on a supported OS | macOS ✔; Win/Linux ✔ (CI) | `python run.py` path exercised on macOS; CI tests + e2e green on all three OSes |
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

## Adversarial implementation reviews (Section 33)

Two independent read-only reviewer agents (security/isolation; accuracy/agenda/history) plus manual passes for functional (A) and cross-platform (G). Every material finding was fixed and, where behavioral, pinned by a test.

Security / isolation (E, F) — isolation held (no cross-workspace path found). Fixed:
- Ollama HTTP client honoured `HTTP_PROXY`/OS proxy and followed redirects → `trust_env=False, follow_redirects=False`.
- Agenda images requested with the token in the URL (and 401'd) → authenticated fetch + blob URL.
- SQLite files world-readable under default umask → `umask 077`, 0700 dirs, 0600 files/token.
- Reference uploads with path separators lost the server id prefix (orphan on delete, overwrite) → sanitize client name first.
- Large uploads spooled to shared OS temp → private `tmp/` in the data dir; 60 MB body cap.
- Repo audit missed `-wal/-shm/.bak`, bare `api_token`, data files under `docs/`, unmarked `.json/.csv` fixtures → rules hardened.
- Minor: parse route now validates workspace; token compare on bytes; IPv6 Host parsing tightened; model output excluded from error text.

Accuracy / agenda / history (B, C, D). Fixed:
- Dedupe merged contradictory or distinct claims → requires same category and calling event, negation-aware, 0.9 overlap.
- Re-approval duplicated decisions/callings/events → `source_note_id` on history rows (migration 0002), idempotent proposals.
- `carry_forward` accepted any follow-up (Case C, completed) → validated against Case B candidates; completed excluded.
- Case B missed manually created follow-ups (NULL `meeting_id`) → NULL-safe query.
- Orphaned/unmapped follow-up notes reached the final list with no finding → high finding; final list only from placed notes; structural checks now run for user notes too.
- User delete/untag/move of a model note was undone by regeneration → every edit marks the note user-origin; user notes take precedence over same-claim model notes on regeneration.
- Single-token / substring owners passed → full name (≥2 tokens), whole-word match. (`[Owner: Pat]` is now withheld by design.)
- 3-character quotes counted as evidence → quotes need ≥12 chars, ≥2 words, not all stopwords.
- Deleting a meeting orphaned follow-ups and skipped audit → deletes its follow-ups too and audits the removed ids.
- Reviewer `add` with high uncertainty stored as verified → stored as `uncertain`.
- Minor: Markdown/newline injection in note or heading text collapsed; NBSP indentation; agenda edits allowed from review states.

Not fixed (documented limitations, ROADMAP): `looks_overconfident` is lexical (now applied to every category). The other limitations listed here earlier were fixed in review H.

## Review H — whole-repo adversarial review (2026-11-08)

Three parallel reviewers (data/history, generation pipeline, security/frontend) found 28 issues plus 10 regressions from the B–F fixes; all fixed, each pinned by a test that fails on the previous code (`test_adv_history.py`, `test_adv_pipeline.py`, `test_adv_security.py`, `frontend/src/meetingSafety.test.tsx`). Several B–F fixes above were superseded:
- Quotes: the 12-char floor rejected real quotes → ≥2 word tokens, at least one not filler; NFC + apostrophe/quote/dash folding.
- Dedupe: same-category requirement kept near-duplicates → merge to the more cautious category, recompute status, never merge differing calling events.
- Edits: any edit made the note a user claim → `Note.user_content`/`source_text` (migration 0003); moves/retags become `user_edited` but keep transcript provenance and full checks; regeneration suppresses claims matching any user-touched note on any node.
- Delete: deletes all meeting content, redacts its audit payloads, `secure_delete` + WAL truncate; follow-ups with outside events are kept but detached (deliberate privacy exception to append-only audit, user-approved).
Other fixes: edits/transcript/agenda refused during processing and job error path made safe; reviewer rewrite/add re-checked and never auto-verified; owner must be a contiguous name in one quote; completed items and tag look-alikes kept out of the final list; prompt delimiters escaped (`PROMPT_VERSION` 1.1); hedged notes no longer become confirmed decisions; event-date forks, lost follow-up notes, `"new"` audit ids, post-approval meeting edits fixed; UI shortcuts ignore selects, failed agenda save blocks approve, review rows no longer remount, Generate flushes autosave; OCR/parsing off the event loop, streaming body cap, DOCX/PDF decompression caps, legacy reference files deleted, repo-audit gaps closed, CI dependency audits now fail the build.

After review H: `tests/test_real_model.py` with qwen3.6:35b-a3b passed (ready_for_approval, 8 notes, ~44 s); the reviewer now strips `[Owner: Rowan Castellano]` because the speaker label is not named, and the final list includes open-question items ("No decision was reached…") the model tagged as follow-ups. qwen3.5:27b, gpt-oss:20b and CI on Windows/Linux not re-run.

## Reviews I–L — four convergence rounds (2026-11-08)

Fresh reviewers per round (data/history, generation pipeline, security/frontend), each round's findings fixed with regression tests (`test_adv_history.py`, `test_adv_pipeline.py`, `test_adv_security_i.py`, `frontend/src/meetingSafety.test.tsx`). Round 4 was the planned last round; its findings are fixed but not re-reviewed.
- Data/history: one-to-one claim pairing (`services._pair_claims`) for regeneration suppression and history matching; agenda text re-imports keep ids; old notes kept until extraction succeeds; withdrawn decisions/callings/events become `unverified` and restore; `worker.lock` so only one app copy runs jobs; migration 0004.
- Pipeline: transcript-driven name rule, number/time check, commitment detection, placement relevance check, render-time discussion markers, crosstalk/ellipsis quote fallbacks (`PROMPT_VERSION` 1.4).
- Security/frontend: OCR outside DB sessions, CSP, PDF parse budget, repo audit (`-z`, staged blobs), agenda Tree/Text/Screenshot flows that never drop edits, serialized transcript saves.

Verified after round 4: 175 backend tests, 25 frontend unit tests, 4 e2e tests, ruff/mypy/audit clean. Real qwen3.6 on a synthetic garbled meeting reaches `ready_for_approval`.

Real-meeting findings (maintainer samples, kept outside the repo; see CLAUDE.md privacy exception), not yet fixed:
- The model reviewer emits many non-actionable HIGH findings (≈90 in one ward council run), so real meetings land in `review_required`. Proposed triage: material only when an actionable action was rejected or unapplied; one model finding per note per round; affirmations become info.
- Notes retell conversation ("A participant mentioned…") and over-redact names that are in the evidence; prompt work needed (rule 4).
- No warning when a transcript doesn't match its agenda/workspace; agenda header blocks (Date, Presiding, Participants) should be set aside as meeting details on import.
- Member-list name suggestions — built 2026-11-08, see below.

## Member-list name suggestions (2026-11-08, uncommitted)

- Members page (per workspace): CSV import (replaces), table, delete-all. Meeting page: suggestion cards (heard text, transcript line, agenda item, candidate + calling, reason) with Confirm / Not this person; answers listed, removable. Migration 0005 (`members`, `name_confirmations`); `PROMPT_VERSION` 1.5 (confirmed-names block). Design and limits: ARCHITECTURE.md.
- Verified: backend 196 passed (21 new in `test_members.py`: parsing, import/replace/delete, isolation, bob boss → Primary teacher ranked first, 30k chars × 500 members under 1 s (≈0.15 s measured), confirm/reject/delete + audit, name check needs the heard text, label, history origin `user`, cascade on meeting delete, audit rule); frontend 28 unit tests (3 new), build, 4 e2e; ruff/mypy/audit clean; prompt-tuning tool tests still pass.
- Not verified: a real member list or a real model run with confirmed names. The glossary-suggestions endpoint (no UI) is left as is.
- Review fixes (two adversarial reviews, correctness + privacy): name provenance stored on the note (`notes.name_confirmed`, in the approval snapshot) instead of computed live; removing a confirmation or editing the transcript re-derives status and re-runs the checks, so an unlicensed name blocks approval; only a whole confirmed name passes, and only when a cited quote (not its line) holds the heard text; cards show how many mentions a confirmation covers; ranking: context only orders equal names, title vs. gendered callings, spouse boost never from the mention itself or between a couple, capitalised organisation names only, no "will teach" pairs; members identified by id; confirm requires a current suggestion, one confirmed name per heard text; CSV suffixes/ISO dates/no-comma names/control characters; list deletion purges "not this person" answers and their audit names; shared WAL checkpoint with a busy warning; tighter repo audit and `.gitignore`. Migration 0006. Verified: backend 217 passed, frontend 29 unit + 4 e2e, ruff/mypy/audit clean, prompt-tuning tests pass.
- Convergence review fixes: a confirmed name now passes even when its first name is also an ordinary lowercase word ("robert"), and "Ask Robert Ross" works; a confirmed heard text covers only mentions of its own, never "Sister Ross" or "Robert Ross" (same rule for the card's count/excerpts and the "in transcript" flag); removing a confirmation clears the label on user-edited notes too; a confirmation removed while a run starts is re-checked before the run ends; simultaneous confirms give "already confirmed" instead of a 500; the unique index ignores case (migration 0006 recreates it and drops case duplicates; ASCII only, `shortcut:`); WAL checkpoint gives up within ~2 s; repo audit catches headerless member rows in every parsed date format, CSV or TSV, and ignores dated prose. Verified: backend 231 passed, frontend 29 unit + 4 e2e, ruff/mypy/audit clean, prompt-tuning 17 passed.
- One shared member list (maintainer decision 2026-11-08): stored once in `<data>/members.sqlite3`, used by both workspaces; routes `/api/members`; Members page says so. Confirmations stay per meeting in each workspace; deleting the list purges "not this person" answers and their audit names in both workspaces and checkpoints all three WALs. Migration 0007 drops the per-workspace `members` table without copying it: reload the CSV once. Verified: backend 234 passed, frontend 29 unit + 4 e2e, build, ruff/mypy/audit clean, prompt-tuning 17 passed.
- Agenda names (maintainer note: agendas may misspell names): draft-agenda spelling suggestions from the member list (Fix spelling / Dismiss, edits only the draft heading), transcript cards say "On the agenda as …" and may rank/lift a member through the agenda's spelling, confirmed names link to the heading naming that person in the prompt and the placement check; `PROMPT_VERSION` 1.7. Design: ARCHITECTURE.md. Verified: backend 238 passed (4 new), frontend 30 unit + 4 e2e, build, ruff/mypy/audit clean, prompt-tuning 17 passed. Not verified: a real agenda/member list or a real-model run (the 0-score bishopric meeting has not been re-scored).
- Agenda-name review fixes + conservative mentions (2026-11-08): an agenda name links and counts as context only when it writes one member's own name (a nickname of the first name counts: "Tom Cope" = Thomas Cope); misspelt or two-person names link nobody; Fix spelling only when one member is clearly closest; the agenda route in ranking is capped at the agenda's likeness to the member; a confirmed name only lowers "May not belong under" to LOW, never for a discussion marker; "Meet Matt Smith" / "Tim Copes" spans; client finds the name itself (emoji offsets); heading whitespace normalised in the prompt (text unchanged, `PROMPT_VERSION` stays 1.7); name check validates the tree. Transcript mentions now need a clear fit (lone words exact and not sentence-initial, thresholds in ARCHITECTURE.md); "bob boss"-style rhymes are no longer suggested (fixtures now use "bob oss"). Verified: backend 240 passed, frontend 31 unit + 4 e2e, build, ruff/mypy/audit clean, prompt-tuning 17 passed. Not verified: re-measure on the real meeting (coordinator).

## Remaining / next steps

1. ~~Push to GitHub and confirm CI on Windows/Linux~~ — done 2026-11-08.
2. Adversarial implementation reviews A–G and fix material findings; record outcomes here.

## Prompt-tuning tool (2026-11-08, uncommitted)

- `tools/prompt_tuning/`: separate uv project (dspy 3.4.1 / gepa 0.1.4, litellm excluded). Tunes only the instruction text of `EXTRACT_INSTRUCTIONS`, scoring through the app's own pipeline and renderer. 12 synthetic meetings with gold in `data/synthetic/`. See its README.
- Verified: tool tests (11), ruff; backend 175 passed; repo audit; a smoke tune ran to completion under `loopback-only.sb` (outbound network blocked, Ollama reachable). Baseline on synthetic data with qwen3.6: mean 0.47. Real meetings scored outside the repo with qwen3.6: 0.31 and 0.08.
- Not verified: an overnight qwen3.5:27b run; resuming from `--run-dir`.
- Found: the 4,096-token output budget can cut off extraction JSON on long or busy meetings (a synthetic meeting scored 0). Name check false positives on "Christmas", "AM", "PM" and sentence-start words.
- 2026-11-08 follow-ups: default max output tokens 4096 → 12288 (long meetings' JSON was cut off: 5 cut-offs in the 11-meeting real baseline); name check no longer flags holidays, AM/PM, mission words or plurals of note words; agenda proposal (no-agenda meetings) now covers every transcript segment and asks for fewer, broader headings (PROMPT_VERSION 1.6). Real 11-meeting baseline with qwen3.6 before these: mean 0.23.

## Hand-off — night of 2026-11-08

- Overnight tuning running: `~/afn-tuning/run-overnight.sh` (qwen3.5:27b, `--data data/synthetic ../../sample_meetings`, 100 meeting runs, sandboxed). Output: `~/afn-tuning/best-27b-names.txt` (+ `.report.txt`), log `~/afn-tuning/tune-27b-names.log`, resumable run dir `~/afn-tuning/run-27b-names`. Re-running the script resumes.
- Morning: confirm the winner with `score` on ALL meetings, twice (per-meeting noise up to ±0.5), against the current prompts.py baseline, same model; only then propose pasting it into `EXTRACT_INSTRUCTIONS` (maintainer approves).
- Real 11-meeting baseline, qwen3.6, before names/heading fixes: mean 0.23 (top problems: missing facts, false "Not discussed", misfiling). Synthetic baseline with names: 0.51.
- Member list: shared store `<data>/members.sqlite3` (301 rows imported by the maintainer). Name suggestions tightened after real-meeting over-matching (84 → 28 on one meeting).
- Open: maintainer answers to answer-key questions (Bishopric 5, Ward Council 6 — listed in the session; deferred); real gold has no `names` yet; agenda header-line detection; reviewer-noise triage; nothing since 5830282 is committed — commit only on explicit approval, and verify `sample_meetings/` and any member files are not staged.

## 2026-11-09/10 — tuned prompt checked, not adopted

- Overnight GEPA run (qwen3.5:27b) finished: winner 0.48 vs 0.33 on its 8 validation meetings, single runs.
- Full check, all 24 meetings × 2 runs, sandboxed (`~/afn-tuning/cmp*.txt`, outside the repo):
  - qwen3.5:27b: current 0.44 vs tuned 0.43 (timed-out runs excluded). Tuned writes "Discussion occurred…" under undiscussed items (wc_04 every run), prefixes notes with labels ("Action assigned:") that the name check flags, and loses facts on the no-agenda meeting. Real meetings +0.06, within noise.
  - qwen3.6:35b-a3b: current 0.41 vs tuned 0.43; real 0.25 vs 0.25; synthetic 0.54 vs 0.59 (fewer wrong follow-up tags). No timeouts.
  - Decision: prompts.py unchanged.
- qwen3.5:27b timed out (600 s Ollama client limit) on 17 of 96 runs, both prompt versions, mostly short synthetic meetings; probably runaway output. Its overnight "0.00 → 0.5" gains were timeouts. Not investigated yet.
- Model comparison, current prompts: real meetings 3.6 0.25 vs 3.5 0.24; synthetic 3.5 ahead when it doesn't time out. One 24-meeting pass: 3.6 ~20–30 min, 3.5 ~3–3.7 h.
- Maintainer answered the 11 answer-key questions (recorded in git-ignored `sample_meetings/open-questions.md`); gold.json edits not yet applied.
- Maintainer's answer-key answers applied to five real gold files (outside git; backups in the session scratchpad). Real-meeting rescore, qwen3.6, current prompts, ×2: 0.25, unchanged (was 0.25). New misses: release-planned notes filed under the follow-up item instead of each name; "called on, no update" never noted; address/records fact not captured anywhere.
- Scoring tool: gold `expected` entries take an optional `also` list of extra headings where the fact counts (agenda lists the same thing twice); false "Not discussed." still checked on `item`. Tool tests 26 passed, ruff/mypy clean. Used for the four releases in Bishopric meeting 3 (maintainer decision).
- All scores above were extraction only (`--review-rounds 0`); the app's default is 3 review rounds, never scored.
- Next experiments (maintainer agreed 2026-11-10), real meetings on qwen3.6, one at a time, app unchanged until results are reviewed:
  1. Review rounds on (3) vs off — done: 0.25 → 0.23, 2.4× slower. Short meetings (≤24k, 3): false "Not discussed" 8 → 0, score 0.32 → 0.36. Long (8): reviewer can't see most of the transcript, mostly removes notes; 0.23 → 0.18.
  2. Review per segment — built: `ModelSettings.review_by_segment` (default False). One review request per extraction segment; each note is shown once per round, in the part holding its first evidence quote; omissions checked in every part. Test `test_review_by_segment_reads_every_part_and_checks_each_note_once` (fails with the setting off). Backend 242 passed. Scorer `--review-by-segment`. Result (`~/afn-tuning/segrev36-*.txt`, 3 rounds, 2 runs): 0.21 (runs 0.22/0.21) vs 0.23 review-on and 0.25 review-off; 3× slower than review-off (8357 s vs 2754 s). Long meetings (8): 0.19 vs 0.18 review-on vs 0.23 off. Short meetings are unaffected by the setting yet moved 0.36 → 0.26 between experiments, so per-meeting noise is about ±0.1–0.2. False "Not discussed" 78 (off 72); reviewer still mostly removes notes. Not worth turning on; setting stays off.
  3. Smaller extraction segments — scorer `--segment-chars` added (tool tests 26 passed). Result 12000 chars, review off (`~/afn-tuning/seg12k36-*.txt`): 0.22 (runs 0.21/0.24; one 600 s timeout in run 1) vs 0.25. Facts found (all 11 meetings, both runs): off 42.9%, 12k chunks 43.5%, thinking 43.2%, review-in-pieces 43.1%, review first part 37.6% — no variant changes how much the model captures.
  4. Thinking on — `ModelSettings.think` (default False; extract/review/agenda requests; OCR untouched), scorer `--think`. Ollama 0.33.3 + qwen3.6 garbles format-constrained JSON when thinking ('{"answer": "}"'), so with think on the provider drops `format` and puts the schema in the prompt; replies are still validated strictly. Test `test_ollama_think.py`; backend 241 passed. No Settings UI yet. Result (`~/afn-tuning/think36-*.txt`, review off, 2 runs): 0.25 → 0.24 (runs 0.25/0.22), 2.5× slower (6949 s vs 2754 s for both passes). Mixed per meeting (Bishopric meeting 2 0.18→0.26, Ward Council meeting 5 0.06→0.18; Bishopric meeting 5 0.43→0.28, Ward Council meeting 1 0.40→0.33); fewer misplaced facts (46→29) but more names flagged (6→14); one run of Bishopric meeting 5 rendered no notes with no processing error. Not worth turning on; setting stays off.
- Adversarial review of the conclusions (2026-11-10): gpt_notes.txt are ChatGPT drafts whose errors the gold lists (`gpt_errors`), so they are not a yardstick; facts found is the fair measure (drafts ~62%, app ~40% on the 9 agenda meetings). Recall does not track transcript length; the score's penalties grow with agenda size (one misfiled fact = missing + misfiled + false "Not discussed"). Run-to-run noise exceeds every variant difference so far; comparisons need ≥3 runs, paired, by facts found.
- Ollama request timeout 600 → 1800 s (`ollama_provider.py`; dense 27–31B models at ~20 tok/s need ~10 min for a full 12k-token reply). Scorer `--save-notes DIR` (refuses repo paths for real data).
- Converged conclusion after 4 adversarial review rounds (2026-11-10; data: one diagnostic run on qwen3.6, notes in `~/afn-tuning/diag36/`, hand-checked):
  1. Facts under the right item ≈ 43% with review off; all review-off variants within noise (42–45%). The default review step (3 rounds) loses facts (37–38%); separate fix or default to 0 (maintainer).
  2. Of ~146 lost facts/run: omission ~60–65% (incl. ~20 keyword-coincidence "misfilings"), real misfiling ~21–24% (mostly parent/child granularity: one group summary instead of per-person notes), hidden by the quote check ~6% (7 of 8 real notes on the right item; cause unknown, quotes not saved), scorer keyword misses ~5%.
  3. App capture regardless of item ≈ 56–59%; ChatGPT drafts ≈ 70% but unaudited for keyword coincidences (drafts are 26% longer) — comparison inconclusive until the fixed scorer runs on both.
  4. No-agenda meetings: proposed headings 19 (max 6) and ~58 (max 9), many sentence-length; notes pile on a few headings, the rest "Not discussed."
  5. Scorer fixes before it steers work: word-start keyword matching; flag single-short-keyword gold entries (130/254 facts have one keyword group); count a misfiled fact once; report capture, filing accuracy, false "Not discussed" (now 36/125 = 29%), wrong content, heading count for no-agenda.
  6. Fix order: omission (best model, then staged "list every outcome, then file" if needed), filing (most-specific-item rule), quote-check misses, review-step loss, no-agenda proposals. prompts.py changes need maintainer approval. 3 runs detect only ≥5-point differences.
  7. Provisional targets: capture ≥ drafts meeting-by-meeting (once both measured with the fixed scorer), filing ≥ 90% of captured (now ~77%), false "Not discussed" ≤ 5%, wrong content near zero, no-agenda proposals within max_headings.
- Overnight model comparison (`~/afn-tuning/run-bakeoff.sh` → `~/afn-tuning/bake/`): qwen3.8:27b, gemma4:31b, qwen3.6:35b-a3b; real meetings, review off, 3 rounds, notes saved.
  - Result (2026-11-11). Old score means: qwen3.8 0.32/0.34/0.37, gemma4 0.28/0.29/0.29, qwen3.6 0.24/0.25/0.27. Pass time (11 meetings): qwen3.8 53–64 min, gemma4 48–56 min, qwen3.6 13–22 min. One Ollama-side timeout each for qwen3.8 and gemma4 ("upstream error: net/http: timeout").
  - Rescored with the fixed scorer (scratchpad `rescore.py`, saved notes only; 9 agenda meetings, all runs): capture / on item / filed right of captured / discussed items shown absent / wrong-content notes per run:
    qwen3.8 72% / 57% / 79% / 23% / 3.0; gemma4 54% / 44% / 81% / 17% / 0.7; qwen3.6 59% / 41% / 70% / 27% / 5.7; ChatGPT drafts 70% / 62% / 89% / 12% / 30 (19 forbidden hits; gold's forbidden list was written from the drafts' errors, so biased against them).
  - Maintainer decision (2026-11-11): recommend qwen3.8:27b as the app model; README and CONTRIBUTING updated. `tests/test_real_model.py` with qwen3.8 passed (ready_for_approval, 14 notes, ~64 s).
  - qwen3.8 matches the drafts on capture; the remaining gap is filing and false "Not discussed". No-agenda heading proposals are far over max for every model (Bishopric meeting 5: 8–20 vs 6; Bishopric meeting 6: 24–85 vs 9).
- Review-step re-test (maintainer asked 2026-11-11 before changing the default): `~/afn-tuning/run-review38.sh` → `~/afn-tuning/review38/`, qwen3.8, 3 review rounds, 3 passes; compare with `bake/qwen3.8-*` (review off). Started 08:00, finished 13:49 (passes 124/94/131 min vs 53–64 min review off).
  - Result (fixed scorer, 9 agenda meetings; capture / on item / filed ok / false ND / wrong per run): review on, per run 69/51/74/24/4, 71/58/81/19/2, 68/53/78/27/3; mean 69% / 54% / 78% / 23% / 3.0. Review off (bake, 3 runs): 72% / 57% / 79% / 23% / 3.0, runs 56–57% on item. So about 3 points fewer facts, no fewer wrong notes, 2× the time. Per-meeting spread is large (e.g. Bishopric meeting 3 on item 12–64% across runs), so per-meeting differences mean little.
  - Ollama-side "timeout awaiting response headers" (502) after ~10 min per request: every review round of Bishopric meeting 2 in passes 1 and 3 (meeting ~2000 s), plus extraction of no-agenda Bishopric meeting 5 (whole meeting, pass 2) and Bishopric meeting 6. A failed review round keeps the notes unchanged (services.py review loop), so it did not remove facts. Our client timeout is 1800 s, so this limit is in Ollama itself; not investigated.
  - Maintainer decision (2026-11-11): review off by default. `ModelSettings.max_review_rounds` default 3 → 0 (deterministic checks still run); README updated. Backend 242 passed, 1 skipped; ruff, format, mypy clean.
  - Not measured: whether review improves wording (softened claims, removed unsupported owners). Needs before/after notes from the same extraction.
- Scorer fixed (tools/prompt_tuning): keywords match at word starts only (no "uti" in "duties"); a misfiled fact is no longer penalised a third time (W_MISFILED removed); `measures()` reports capture, on-item, false-absent items and wrong content, printed by `score`. Tool tests 28 passed, ruff/mypy clean.
