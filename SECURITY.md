# Security

Agenda First Notes is a single-user application that runs on your own computer. This document describes what it protects against, how, and what it does not protect against.

## Threat model

Assets: meeting transcripts, agendas, notes, follow-up history, calling records, reference documents — all potentially confidential.

In scope:
- Other software on the same machine (including a malicious web page in your browser) reaching the local API.
- DNS-rebinding and cross-site request attacks against the localhost server.
- Prompt injection through transcripts or reference documents.
- Accidental data leakage: uploads escaping the data directory, real data committed to the public repository, secrets in logs.
- Cross-workspace contamination (Ward Council ↔ Bishopric).

Out of scope (documented limitations):
- A compromised OS user account, malware with your privileges, or physical access to an unencrypted disk.
- The configuration of Ollama itself (a separate process).
- Copies of data made by OS backup or sync tools.

## Local networking

- The server binds to `127.0.0.1` only (`cli.py serve`). It never listens on other interfaces.
- `security.LocalOnlyMiddleware` rejects any request whose `Host` header is not a loopback host (400) — this defeats DNS rebinding — and any request with a non-loopback `Origin` (403), which blocks cross-site requests from web pages. Dev origins can be added with `AFN_DEV_ORIGINS` for the Vite dev server only.
- Every `/api` request (except `/api/health`) must carry the header `X-AFN-Token` (query-string tokens are never accepted; the UI loads images via fetch + blob URLs) matching a per-installation random token stored with mode 0600 in the data directory. The browser receives it once through the launch URL fragment (`#token=…`, which browsers never send to servers) and keeps it in `sessionStorage`. Other local processes that do not know the token get 401.
- Custom header + Origin check + no cookies = no CSRF surface. There are no state-changing GET endpoints (tested in `test_no_state_changing_get_routes`).
- Responses carry `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`. OpenAPI/docs endpoints are disabled.

## File handling

- Images are validated with Pillow (`Image.verify`) and must be PNG, JPEG or WebP ≤ 15 MB. Transcripts ≤ 20 MB (`.txt`/`.md` only). Reference documents ≤ 50 MB (`.txt`/`.md`/`.pdf`/`.docx`), text extracted locally with `pypdf` / `python-docx`.
- Stored filenames are server-generated (`<meeting_id>_<image_id>.<ext>`) or flattened to a single safe path component (`workspaces.safe_filename`). `Workspace.file_path()` resolves inside the workspace folder only and refuses symlinks and escapes (tested).
- Multipart uploads larger than 1 MB are spooled by the web framework to a temp directory; at startup the app points Python's temp directory at a private `tmp/` folder (mode 0700) inside the data directory, so nothing lands in the shared OS temp dir. Request bodies above 60 MB are rejected before being read.

## Data storage

- Runtime data lives under the OS app-data directory (`platformdirs`), never inside the repository; `config.assert_outside_repo()` refuses to start otherwise (tested).
- The process runs with `umask 077`; data directories are created 0700 and SQLite/WAL files, token and settings 0600 where the OS supports POSIX modes (the token file is opened with 0600 from the first byte).
- **Not encrypted at rest.** Use full-disk encryption. We do not claim otherwise.
- Settings (`settings.json`) hold only model configuration. No credentials exist anywhere.
- Logging: uvicorn access logs are disabled; application errors return short messages without request bodies; transcripts are never written to logs.

## Workspace separation

Each workspace has its own SQLite database and directory. Workspace ids are a closed set validated on every request; database engines are bound per workspace, so there is no query that could join across them. Model prompts are built only from the current meeting's approved agenda, transcript segment and workspace profile. Tests put identical names/topics/dates in both workspaces and assert that lists, search, follow-ups, audit, exports and model prompts never cross (`test_identical_content_in_both_workspaces_never_crosses`).

## Prompt injection

Transcripts and reference documents are untrusted. The model:
- has no tools, no function calling, no file or network access — it only returns JSON matching a fixed schema;
- cannot change settings, records or files: every state change goes through validated services with Pydantic-validated inputs;
- cannot cite evidence that does not exist: quotes are located in the real transcript or the note is marked unsupported.

The worst a hostile transcript can do is produce a note that says what the transcript says — and even that is flagged if it is unsupported or claims an owner that is not named. Tested in `test_prompt_injection_in_transcript_is_inert`.

Reference documents are never passed to the model as evidence. The only use today is advisory glossary spelling suggestions that require user confirmation.

## Inference stays local

`OllamaProvider` refuses any host that is not loopback, at construction and when settings are saved (400). Its HTTP client ignores `HTTP_PROXY`/`HTTPS_PROXY` and OS proxy settings (`trust_env=False`) and does not follow redirects, so a proxy or a 3xx cannot route a transcript off the machine. There is no second provider, no fallback and no retry against a different model. The app does not download models. `AFN_PROVIDER=fake` enables a rule-based stand-in for tests/CI only; it never calls anything.

## Repository publication safeguards

Defense in depth against committing real data:
1. Runtime data is outside the worktree by construction (and tested).
2. `.gitignore` covers databases, tokens, settings, uploads, exports, logs, HEIC.
3. `agenda-first-notes audit [--staged]` (cross-platform Python) flags database (incl. `-wal`/`-shm`/`.bak`)/key/env/document files anywhere outside the two fixture folders, bare runtime files (`api_token`, `settings.json`), filenames suggesting meeting or member data outside fixture/docs folders, large files, secret-looking strings, and any `.txt/.json/.csv` under fixture or docs folders lacking a `SYNTHETIC` marker. Runs in pre-commit and CI.
4. `.pre-commit-config.yaml` adds gitleaks, private-key detection and large-file checks.
5. CI runs gitleaks, `pip-audit` and `npm audit`.

All fixtures, screenshots and examples must be fictional.

## Known limitations

- No authentication beyond the local token; anyone who can read your browser session or the token file is you.
- Deleting records does not scrub SQLite free pages or external backups.
- The lexical over-confidence check and the model reviewer reduce, but cannot eliminate, inaccurate summaries. Human approval is required for that reason.
- Restricted organizational data: see README. The application cannot tell whether you are authorized to process a given document.

## Reporting

Open a GitHub issue with the `security` label, or contact the maintainer privately if the issue could expose user data. Do not include real meeting content in reports.
