"""Regression tests for adversarial-review findings: body limits, blocking uploads, parser bombs,
reference deletion and repository-audit gaps. SYNTHETIC data only."""

from __future__ import annotations

import inspect
import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agenda_first_notes import api, ingest, repo_audit, security


def test_chunked_body_over_limit_is_refused(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "MAX_BODY_BYTES", 1024)
    mid = client.post("/api/w/ward_council/meetings", json={}).json()["id"]

    def chunks():  # no Content-Length: httpx sends Transfer-Encoding: chunked
        yield b'{"text": "SYNTHETIC '
        for _ in range(20):
            yield b"x" * 200
        yield b'"}'

    r = client.put(
        f"/api/w/ward_council/meetings/{mid}/transcript",
        content=chunks(),
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 413
    assert client.get(f"/api/w/ward_council/meetings/{mid}").json()["transcript"] is None
    boundary = b"SYNTHETICBOUNDARY"
    parts = [b"--" + boundary + b'\r\nContent-Disposition: form-data; name="file"; filename="g.txt"\r\n\r\n']
    parts += [b"x" * 200] * 20 + [b"\r\n--" + boundary + b"--\r\n"]
    r = client.post(
        "/api/w/ward_council/references",
        content=iter(parts),
        headers={"content-type": "multipart/form-data; boundary=SYNTHETICBOUNDARY"},
    )
    assert r.status_code == 413
    assert client.get("/api/w/ward_council/references").json() == []


def test_small_chunked_body_still_works(client: TestClient) -> None:
    mid = client.post("/api/w/ward_council/meetings", json={}).json()["id"]
    r = client.put(
        f"/api/w/ward_council/meetings/{mid}/transcript",
        content=iter([b'{"text": "SYNTHETIC ', b'short"}']),
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 200


def test_slow_parsing_routes_do_not_block_the_event_loop() -> None:
    # Sync routes run in FastAPI's threadpool; async ones would run OCR / PDF parsing on the event loop.
    for route in (api.upload_agenda_image, api.upload_reference):
        assert not inspect.iscoroutinefunction(route), route.__name__


def _docx(text: str) -> bytes:
    import docx

    d = docx.Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_docx_decompressed_size_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _docx("SYNTHETIC " + "word " * 2000)
    assert "SYNTHETIC" in ingest.extract_document_text("a.docx", data)[0]
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        expanded = sum(i.file_size for i in z.infolist())
    monkeypatch.setattr(ingest, "MAX_DOC_EXPANDED_BYTES", expanded - 1)
    with pytest.raises(ValueError, match="too large"):
        ingest.extract_document_text("a.docx", data)
    with pytest.raises(ValueError, match="not a valid DOCX"):
        ingest.extract_document_text("a.docx", b"not a zip")


def _pdf(content: bytes) -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject

    w = PdfWriter()
    page = w.add_blank_page(200, 200)
    stream = DecodedStreamObject()
    stream.set_data(content)
    page.replace_contents(stream)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_pdf_decompressed_size_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _pdf(b"q " * 5000 + b"Q")
    ingest.extract_document_text("a.pdf", data)
    monkeypatch.setattr(ingest, "MAX_DOC_EXPANDED_BYTES", 1000)
    with pytest.raises(ValueError, match="too large"):
        ingest.extract_document_text("a.pdf", data)


def test_reference_upload_rejects_docx_bomb(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ingest, "MAX_DOC_EXPANDED_BYTES", 100)
    r = client.post("/api/w/ward_council/references", files={"file": ("g.docx", _docx("SYNTHETIC"), "application/x")})
    assert r.status_code == 400


def test_delete_reference_removes_files_from_older_naming_schemes(client: TestClient, data_dir: Path) -> None:
    r = client.post(
        "/api/w/ward_council/references", files={"file": ("glossary.txt", b"SYNTHETIC Glossary", "text/plain")}
    )
    doc_id = r.json()["id"]
    refs = data_dir / "workspaces" / "ward_council" / "references"
    legacy = [refs / f"{doc_id}_doc", refs / f"{doc_id}_glossary (raw)!.txt"]  # pre-safe_filename names
    for p in legacy:
        p.write_text("SYNTHETIC")
    other = refs / "deadbeef_keep.txt"
    other.write_text("SYNTHETIC")
    assert client.delete(f"/api/w/ward_council/references/{doc_id}").status_code == 204
    assert [p.name for p in refs.iterdir()] == [other.name]


# ---------------------------------------------------------------- repository audit


def _audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rel: str, text: str = "x") -> list[str]:
    monkeypatch.setattr(repo_audit, "REPO_ROOT", tmp_path)
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return repo_audit.audit_paths([p])


@pytest.mark.parametrize(
    "rel",
    [
        "api_token",
        "backend/api_token.txt",
        "api_token.bak",
        "frontend/api_token",
        "settings.json",
        "settings.json.bak",
        "settings.json~",
        "bishopric_2026-03-01_1a2b3c4d.md",  # the app's own export file names
        "ward_council_undated_1a2b3c4d.txt",
    ],
)
def test_audit_flags_runtime_files_and_exports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rel: str) -> None:
    assert _audit(tmp_path, monkeypatch, rel)


@pytest.mark.parametrize(
    "rel",
    ["backend/agenda_first_notes/api.py", "backend/agenda_first_notes/config.py", "frontend/.vscode/settings.json"],
)
def test_audit_does_not_flag_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rel: str) -> None:
    assert _audit(tmp_path, monkeypatch, rel, "def api_token() -> str: ...") == []


def test_audit_requires_marker_in_markdown_fixtures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _audit(tmp_path, monkeypatch, "backend/tests/fixtures/meeting.md", "Speaker: hello")
    assert _audit(tmp_path, monkeypatch, "backend/tests/fixtures/meeting2.md", "SYNTHETIC Speaker: hello") == []
    assert _audit(tmp_path, monkeypatch, "docs/GUIDE.md", "prose") == []


def test_staged_audit_includes_renames(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    class Out:
        stdout = ""

    monkeypatch.setattr(repo_audit.subprocess, "run", lambda cmd, **_: seen.append(cmd) or Out())
    repo_audit._files(staged_only=True)
    assert "--diff-filter=ACMR" in seen[0]
