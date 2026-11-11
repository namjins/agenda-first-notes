"""Regression tests for adversarial review I: OCR lock, upload state guard, CSP, PDF parse budget and
repository-audit gaps. SYNTHETIC data only."""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from agenda_first_notes import ingest, repo_audit, services
from agenda_first_notes.models import Meeting
from agenda_first_notes.workspaces import get_workspace, session_for


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, "PNG")
    return buf.getvalue()


def _upload(client: TestClient, mid: str):  # type: ignore[no-untyped-def]
    return client.post(
        f"/api/w/ward_council/meetings/{mid}/agenda/images", files={"file": ("a.png", _png(), "image/png")}
    )


def test_ocr_runs_without_holding_the_workspace_write_lock(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    mid = client.post("/api/w/ward_council/meetings", json={}).json()["id"]
    ws = get_workspace("ward_council")

    def ocr_while_writing(_data: bytes) -> tuple[str, str]:
        # Another writer (autosave, the job worker) during a slow OCR call; it got "database is locked" before.
        services.set_transcript(ws, mid, "SYNTHETIC written during OCR", source="paste")
        return "SYNTHETIC agenda", "vision_model"

    monkeypatch.setattr(ingest, "extract_agenda_text", ocr_while_writing)
    r = _upload(client, mid)
    assert r.status_code == 200
    assert r.json()["proposed_text"] == "SYNTHETIC agenda"
    m = client.get(f"/api/w/ward_council/meetings/{mid}").json()
    assert m["transcript"]["version_no"] == 1
    assert len(m["images"]) == 1 and m["images"][0]["extracted_text"] == "SYNTHETIC agenda"


@pytest.mark.parametrize("state", ["approved", "processing"])
def test_agenda_image_refused_on_approved_or_processing_meeting(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    mid = client.post("/api/w/ward_council/meetings", json={}).json()["id"]
    with session_for(get_workspace("ward_council")) as s:
        m = s.get(Meeting, mid)
        assert m is not None
        m.state = state
        s.commit()
    monkeypatch.setattr(ingest, "extract_agenda_text", lambda _d: ("SYNTHETIC", "vision_model"))
    assert _upload(client, mid).status_code == 400
    assert client.get(f"/api/w/ward_council/meetings/{mid}").json()["images"] == []


def test_responses_forbid_framing(client: TestClient) -> None:
    csp = client.get("/api/health").headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "default-src 'self'" in csp


def _pdf_with_form(pages: int, calls: int, form_bytes: int) -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject

    w = PdfWriter()
    form = DecodedStreamObject()
    form.set_data(b"BT (SYNTHETIC) Tj ET " + b"q Q " * (form_bytes // 4))
    form.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Form"),
            NameObject("/BBox"): ArrayObject([FloatObject(0), FloatObject(0), FloatObject(200), FloatObject(200)]),
        }
    )
    ref = w._add_object(form)
    for _ in range(pages):
        page = w.add_blank_page(200, 200)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/XObject"): DictionaryObject({NameObject("/Fm1"): ref})}
        )
        content = DecodedStreamObject()
        content.set_data(b"/Fm1 Do " * calls)
        page.replace_contents(content)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_pdf_budget_counts_form_xobjects_on_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ingest, "MAX_DOC_EXPANDED_BYTES", 50_000)
    assert ingest.extract_document_text("a.pdf", _pdf_with_form(2, 1, 10_000))[1] == "pdf"  # within budget
    # Tiny page streams, one shared form: the old cap counted ~8 bytes per page and parsed the form every time.
    with pytest.raises(ValueError, match="too large"):
        ingest.extract_document_text("a.pdf", _pdf_with_form(20, 1, 10_000))


def test_pdf_budget_counts_repeated_form_invocations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ingest, "MAX_DOC_EXPANDED_BYTES", 50_000)
    with pytest.raises(ValueError, match="too large"):
        ingest.extract_document_text("a.pdf", _pdf_with_form(1, 20, 10_000))


def _xobject(w, body: bytes, subtype: str = "/Form", resources=None):  # type: ignore[no-untyped-def]
    from pypdf.generic import ArrayObject, DecodedStreamObject, FloatObject, NameObject, NumberObject

    x = DecodedStreamObject()
    x.set_data(body)
    x.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject(subtype)})
    if subtype == "/Image":
        x.update({NameObject(k): NumberObject(1) for k in ("/Width", "/Height")})
        x.update(
            {NameObject("/ColorSpace"): NameObject("/DeviceGray"), NameObject("/BitsPerComponent"): NumberObject(8)}
        )
    else:
        x[NameObject("/BBox")] = ArrayObject([FloatObject(0), FloatObject(0), FloatObject(9), FloatObject(9)])
    if resources is not None:
        x[NameObject("/Resources")] = resources
    return w._add_object(x)


def _xres(**names):  # type: ignore[no-untyped-def]
    from pypdf.generic import DictionaryObject, NameObject

    return DictionaryObject(
        {NameObject("/XObject"): DictionaryObject({NameObject("/" + k): v for k, v in names.items()})}
    )


def _page(w, content: bytes, resources=None) -> None:  # type: ignore[no-untyped-def]
    from pypdf.generic import DecodedStreamObject, NameObject

    p = w.add_blank_page(200, 200)
    if resources is not None:
        p[NameObject("/Resources")] = resources
    c = DecodedStreamObject()
    c.set_data(content)
    p.replace_contents(c)


def _bytes(w) -> bytes:  # type: ignore[no-untyped-def]
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


BIG = b"BT (SYNTHETIC) Tj ET " + b"q Q " * 2_500  # ~10 KB of content


def test_pdf_budget_charges_non_form_subtypes_pypdf_still_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    from pypdf import PdfWriter

    monkeypatch.setattr(ingest, "MAX_DOC_EXPANDED_BYTES", 50_000)
    w = PdfWriter()
    _page(w, b"/F Do " * 100, _xres(F=_xobject(w, BIG, subtype="/PS")))  # not /Form, not /Image: parsed as a form
    with pytest.raises(ValueError, match="too large"):
        ingest.extract_document_text("a.pdf", _bytes(w))


def test_pdf_budget_refuses_cyclic_forms(monkeypatch: pytest.MonkeyPatch) -> None:
    from pypdf import PdfWriter
    from pypdf.generic import NameObject

    w = PdfWriter()
    f2 = _xobject(w, b"/A Do")
    f1 = _xobject(w, b"/B Do " + BIG, resources=_xres(B=f2))
    f2.get_object()[NameObject("/Resources")] = _xres(A=f1)
    _page(w, b"/A Do ", _xres(A=f1))
    _page(w, b"/B Do " * 1000, _xres(B=f2))  # was charged 0 per call once the cycle memoized F2 at 0
    with pytest.raises(ValueError, match="cyclic"):
        ingest.extract_document_text("a.pdf", _bytes(w))


def test_pdf_budget_follows_resources_inherited_from_the_page_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    from pypdf import PdfWriter
    from pypdf.generic import NameObject

    monkeypatch.setattr(ingest, "MAX_DOC_EXPANDED_BYTES", 50_000)
    w = PdfWriter()
    f = _xobject(w, BIG)
    _page(w, b"/F Do " * 100)
    page = w.pages[0]
    del page[NameObject("/Resources")]
    w._root_object["/Pages"].get_object()[NameObject("/Resources")] = _xres(F=f)
    with pytest.raises(ValueError, match="too large"):
        ingest.extract_document_text("a.pdf", _bytes(w))


def test_pdf_budget_charges_only_the_named_form_not_images_or_text(monkeypatch: pytest.MonkeyPatch) -> None:
    from pypdf import PdfWriter

    monkeypatch.setattr(ingest, "MAX_DOC_EXPANDED_BYTES", 60_000)
    w = PdfWriter()
    images = {f"Im{i}": _xobject(w, b"\x00", subtype="/Image") for i in range(20)}
    res = _xres(Logo=_xobject(w, BIG), **images)
    content = b"q /Logo Do Q " + b"".join(b"q /Im%d Do Q " % i for i in range(20)) + b"BT (Do Do Do Do) Tj ET"
    for _ in range(5):  # letterhead once per page: 5 x ~10 KB, well within budget
        _page(w, content, res)
    assert ingest.extract_document_text("a.pdf", _bytes(w))[1] == "pdf"


# ---------------------------------------------------------------- repository audit


def _git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, names: list[str]) -> list[str]:
    monkeypatch.setattr(repo_audit, "REPO_ROOT", tmp_path)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for n in names:
        p = tmp_path / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", "."], check=True)
    return repo_audit.audit_paths(repo_audit._files(staged_only=True))


def test_audit_checks_non_ascii_file_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    name = "Bishopric agenda – réunion.md"  # git quotes this name unless -z is used
    problems = _git_repo(tmp_path, monkeypatch, [name])
    assert any(p.startswith(name) for p in problems)
    monkeypatch.setattr(repo_audit, "REPO_ROOT", tmp_path)
    assert any(p.name == name for p in repo_audit._files(staged_only=False))


@pytest.mark.parametrize(
    "rel",
    [
        "api_token copy",
        "api_token-old",
        "api_token (1)",
        "backend/api_token_backup",
        "settings (1).json",
        "settings-old.json",
        "docs/bishopric_2026-03-01_1a2b3c4d.md",  # exports are flagged in docs/ too
        "notes/ward_council_undated_1a2b3c4d.txt",
        "settings_old.json",
        "settings2.json",
        "frontend/settings.json~",
        "frontend/settings.json.old",
        "api_token.txt.sample",  # a template suffix never rescues an api_token name
    ],
)
def test_audit_flags_runtime_file_and_export_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rel: str
) -> None:
    monkeypatch.setattr(repo_audit, "REPO_ROOT", tmp_path)
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")
    assert repo_audit.audit_paths([p])


@pytest.mark.parametrize("rel", [".env.example", "settings.json.sample", "backend/agenda_first_notes/settings.py"])
def test_audit_allows_committed_templates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rel: str) -> None:
    monkeypatch.setattr(repo_audit, "REPO_ROOT", tmp_path)
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")
    assert repo_audit.audit_paths([p]) == []


def test_staged_audit_reads_the_staged_blob_not_the_working_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "token = " + '"' + "SYNTHETIC" + "x" * 16 + '"\n'  # assembled at runtime: this file must pass the audit
    monkeypatch.setattr(repo_audit, "REPO_ROOT", tmp_path)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "notes.py").write_text(secret)
    subprocess.run(["git", "-C", str(tmp_path), "add", "notes.py"], check=True)
    (tmp_path / "notes.py").write_text("clean = 1\n")  # unstaged cleanup must not hide what will be committed
    assert any("secret" in p for p in repo_audit.audit_paths(repo_audit._files(staged_only=True), staged=True))
    assert repo_audit.audit_paths([tmp_path / "notes.py"]) == []  # whole-tree mode reads the working copy


def test_staged_audit_handles_colon_names_and_checks_names_without_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(repo_audit, "REPO_ROOT", tmp_path)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    if sys.platform != "win32":  # ':' is not allowed in Windows file names
        (tmp_path / "0:notes.py").write_text("token = " + '"' + "SYNTHETIC" + "y" * 16 + '"\n')
    (tmp_path / "api_token").write_text("x")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    (
        tmp_path / "api_token"
    ).unlink()  # tracked but gone from the working tree: no content, the name check still applies
    assert any(p.startswith("api_token:") for p in repo_audit.audit_paths(repo_audit._files(staged_only=False)))
    problems = repo_audit.audit_paths(repo_audit._files(staged_only=True), staged=True)
    assert any(p.startswith("api_token:") for p in problems)
    if sys.platform != "win32":
        assert any(p.startswith("0:notes.py") and "secret" in p for p in problems)
