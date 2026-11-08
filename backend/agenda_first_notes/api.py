"""HTTP API. Every workspace-scoped route takes the workspace id in the path and validates it."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import config, history, ingest, jobs, services
from . import search as search_mod
from .agenda import AgendaNode, from_dicts, parse_text, to_dicts, to_text
from .llm import get_provider
from .llm.base import ProviderError
from .models import (
    CALLING_EVENTS,
    AgendaImage,
    AgendaRevision,
    Job,
    Meeting,
    Note,
    NotesRevision,
    ReferenceDocument,
    ReviewFinding,
    TranscriptVersion,
)
from .workspaces import WORKSPACES, UnknownWorkspace, Workspace, get_workspace, session_for

router = APIRouter(prefix="/api")


def ws_dep(workspace_id: str) -> Workspace:
    try:
        return get_workspace(workspace_id)
    except UnknownWorkspace as e:
        raise HTTPException(404, str(e)) from e


WS = Annotated[Workspace, Depends(ws_dep)]


def _err(e: Exception) -> HTTPException:
    if isinstance(e, services.NotFound):
        return HTTPException(404, str(e))
    if isinstance(e, (services.ServiceError, ValueError)):
        return HTTPException(400, str(e))
    if isinstance(e, ProviderError):
        return HTTPException(502, str(e))
    raise e


def _dump(obj: Any) -> dict[str, Any]:
    return {c.key: getattr(obj, c.key) for c in obj.__table__.columns}  # type: ignore[attr-defined]


# ------------------------------------------------------------------ global


@router.get("/health")
def health() -> dict[str, Any]:
    from . import __version__

    return {"ok": True, "version": __version__}


@router.get("/workspaces")
def workspaces() -> list[dict[str, str]]:
    return [{"id": k, "name": v} for k, v in WORKSPACES.items()]


@router.get("/settings")
def get_settings() -> dict[str, Any]:
    return config.load_settings().model_dump()


@router.put("/settings")
def put_settings(body: config.GlobalSettings) -> dict[str, Any]:
    from .llm import set_provider
    from .llm.ollama_provider import assert_local_host

    try:
        assert_local_host(body.models.ollama_host)
    except ProviderError as e:
        raise HTTPException(400, str(e)) from e
    old = config.load_settings()
    config.save_settings(body)
    if old.models.ollama_host != body.models.ollama_host and config.data_dir():
        import os

        if os.environ.get("AFN_PROVIDER") != "fake":
            set_provider(None)
    return body.model_dump()


@router.get("/models")
def models() -> dict[str, Any]:
    p = get_provider()
    h = p.health()
    if not h.get("connected"):
        return {
            **h,
            "models": [],
            "warnings": ["Ollama is not reachable. Start it locally and refresh."],
        }
    try:
        infos = p.list_models()
    except ProviderError as e:
        return {"connected": False, "host": h.get("host"), "models": [], "warnings": [str(e)]}
    s = config.load_settings().models
    warnings = []
    if not infos:
        warnings.append("No local models installed. Pull one with Ollama (e.g. `ollama pull <model>`) then refresh.")
    if s.primary_model and s.primary_model not in {m.name for m in infos}:
        warnings.append(f"Selected primary model {s.primary_model!r} is not installed.")
    return {
        "connected": True,
        "host": h.get("host"),
        "models": [m.__dict__ for m in infos],
        "warnings": warnings,
        "ocr_backends": ingest.ocr_backends(),
    }


@router.post("/models/test")
def test_model(body: dict[str, str]) -> dict[str, Any]:
    try:
        return ingest.test_inference(body.get("model", ""))
    except ProviderError as e:
        raise HTTPException(502, str(e)) from e


# ------------------------------------------------------------------ meetings


class MeetingIn(BaseModel):
    title: str = ""
    meeting_date: str | None = None


class MeetingPatch(BaseModel):
    title: str | None = None
    meeting_date: str | None = Field(default="__unset__")
    carry_forward: list[str] | None = None


def _meeting_out(s: Any, m: Meeting) -> dict[str, Any]:
    d = _dump(m)
    agenda = services.current_agenda(s, m.id)
    approved = services.current_agenda(s, m.id, approved_only=True)
    tv = services.latest_transcript(s, m.id)
    d["agenda"] = (
        {
            "id": agenda.id,
            "revision_no": agenda.revision_no,
            "tree": agenda.tree,
            "source": agenda.source,
            "approved_at": agenda.approved_at,
            "text": to_text(from_dicts(agenda.tree)),
        }
        if agenda
        else None
    )
    d["agenda_approved"] = bool(approved and agenda and approved.id == agenda.id)
    d["transcript"] = (
        {"id": tv.id, "version_no": tv.version_no, "length": len(tv.text), "source": tv.source} if tv else None
    )
    d["images"] = [
        _dump(i)
        for i in s.scalars(select(AgendaImage).where(AgendaImage.meeting_id == m.id).order_by(AgendaImage.position))
    ]
    return d


@router.get("/w/{workspace_id}/meetings")
def list_meetings(ws: WS, state: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    with session_for(ws) as s:
        q = (
            select(Meeting)
            .order_by(Meeting.meeting_date.desc().nulls_last(), Meeting.created_at.desc())
            .limit(max(1, min(limit, 1000)))
        )
        if state:
            q = q.where(Meeting.state == state)
        return [_dump(m) for m in s.scalars(q)]


@router.post("/w/{workspace_id}/meetings", status_code=201)
def create_meeting(ws: WS, body: MeetingIn) -> dict[str, Any]:
    try:
        m = services.create_meeting(ws, body.title, body.meeting_date)
    except services.ServiceError as e:
        raise _err(e) from e
    with session_for(ws) as s:
        return _meeting_out(s, services.get_meeting(s, m.id))


@router.get("/w/{workspace_id}/meetings/{meeting_id}")
def get_meeting(ws: WS, meeting_id: str) -> dict[str, Any]:
    with session_for(ws) as s:
        try:
            return _meeting_out(s, services.get_meeting(s, meeting_id))
        except services.NotFound as e:
            raise _err(e) from e


@router.patch("/w/{workspace_id}/meetings/{meeting_id}")
def patch_meeting(ws: WS, meeting_id: str, body: MeetingPatch) -> dict[str, Any]:
    try:
        kwargs: dict[str, Any] = {"title": body.title, "carry_forward": body.carry_forward}
        if body.meeting_date != "__unset__":
            kwargs["meeting_date"] = body.meeting_date
        services.update_meeting(ws, meeting_id, **kwargs)
    except services.ServiceError as e:
        raise _err(e) from e
    return get_meeting(ws, meeting_id)


@router.delete("/w/{workspace_id}/meetings/{meeting_id}", status_code=204)
def delete_meeting(ws: WS, meeting_id: str) -> None:
    try:
        services.delete_meeting(ws, meeting_id)
    except services.ServiceError as e:
        raise _err(e) from e
    with session_for(ws) as s:
        from sqlalchemy import text

        s.execute(text("DELETE FROM search_index WHERE meeting_id = :m"), {"m": meeting_id})
        s.commit()


@router.post("/w/{workspace_id}/meetings/{meeting_id}/reopen")
def reopen(ws: WS, meeting_id: str) -> dict[str, Any]:
    try:
        services.reopen_meeting(ws, meeting_id)
    except services.ServiceError as e:
        raise _err(e) from e
    return get_meeting(ws, meeting_id)


# ------------------------------------------------------------------ agenda


class AgendaIn(BaseModel):
    text: str | None = None
    tree: list[AgendaNode] | None = None
    source: str = "manual"


@router.put("/w/{workspace_id}/meetings/{meeting_id}/agenda")
def put_agenda(ws: WS, meeting_id: str, body: AgendaIn) -> dict[str, Any]:
    try:
        services.set_agenda(
            ws,
            meeting_id,
            body.tree,
            text=body.text,
            source=body.source if body.source in ("manual", "text", "image", "edit") else "manual",
        )
    except (services.ServiceError, ValueError) as e:
        raise _err(e) from e
    return get_meeting(ws, meeting_id)


@router.post("/w/{workspace_id}/meetings/{meeting_id}/agenda/approve")
def approve_agenda(ws: WS, meeting_id: str) -> dict[str, Any]:
    try:
        services.approve_agenda(ws, meeting_id)
    except services.ServiceError as e:
        raise _err(e) from e
    return get_meeting(ws, meeting_id)


@router.get("/w/{workspace_id}/meetings/{meeting_id}/agenda/revisions")
def agenda_revisions(ws: WS, meeting_id: str) -> list[dict[str, Any]]:
    with session_for(ws) as s:
        return [
            _dump(r)
            for r in s.scalars(
                select(AgendaRevision)
                .where(AgendaRevision.meeting_id == meeting_id)
                .order_by(AgendaRevision.revision_no)
            )
        ]


@router.post("/w/{workspace_id}/meetings/{meeting_id}/agenda/images")
async def upload_agenda_image(ws: WS, meeting_id: str, file: UploadFile = File(...)) -> dict[str, Any]:  # noqa: B008
    data = await file.read()
    try:
        fmt = ingest.validate_image(data)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    with session_for(ws) as s:
        services.get_meeting(s, meeting_id)
        count = len(s.scalars(select(AgendaImage).where(AgendaImage.meeting_id == meeting_id)).all())
        img = AgendaImage(meeting_id=meeting_id, filename="", sha256=services.sha256(data), position=count)
        s.add(img)
        s.flush()
        img.filename = f"{meeting_id}_{img.id}.{fmt}"
        path = ws.file_path(ws.uploads, img.filename)
        path.write_bytes(data)
        extracted, backend, error = None, None, None
        try:
            extracted, backend = ingest.extract_agenda_text(data)
            img.extracted_text = extracted
        except (ValueError, ProviderError) as e:
            error = str(e)
        s.commit()
        return {"image": _dump(img), "backend": backend, "error": error, "proposed_text": extracted}


@router.get("/w/{workspace_id}/meetings/{meeting_id}/agenda/images/{image_id}")
def get_agenda_image(ws: WS, meeting_id: str, image_id: str) -> Any:
    from fastapi.responses import FileResponse

    with session_for(ws) as s:
        img = s.get(AgendaImage, image_id)
        if img is None or img.meeting_id != meeting_id:
            raise HTTPException(404, "image not found")
        path = ws.file_path(ws.uploads, img.filename)
        if not path.exists():
            raise HTTPException(404, "image file missing")
        return FileResponse(path, media_type=f"image/{'jpeg' if path.suffix == '.jpg' else path.suffix[1:]}")


@router.post("/w/{workspace_id}/meetings/{meeting_id}/agenda/propose")
def propose_agenda(ws: WS, meeting_id: str) -> dict[str, Any]:
    """Agenda-free meetings: propose headings from the current transcript only."""
    from . import pipeline

    with session_for(ws) as s:
        services.get_meeting(s, meeting_id)
        tv = services.latest_transcript(s, meeting_id)
        if tv is None:
            raise HTTPException(400, "Add a transcript first.")
        text = tv.text
    settings = config.load_settings().models
    if not settings.primary_model:
        raise HTTPException(400, "No primary model selected.")
    try:
        tree = pipeline.propose_agenda(get_provider(), settings, ws.id, text)
        services.set_agenda(ws, meeting_id, tree, source="manual" if not tree else "text") if tree else None
    except (ProviderError, pipeline.PipelineError, services.ServiceError) as e:
        raise _err(e if not isinstance(e, pipeline.PipelineError) else services.ServiceError(str(e))) from e
    return {"tree": to_dicts(tree), "meeting": get_meeting(ws, meeting_id)}


@router.post("/w/{workspace_id}/agenda/parse")
def parse_agenda_text(body: dict[str, str]) -> dict[str, Any]:
    return {"tree": to_dicts(parse_text(body.get("text", "")))}


# ------------------------------------------------------------------ transcript


class TranscriptIn(BaseModel):
    text: str
    reason: str | None = None


@router.put("/w/{workspace_id}/meetings/{meeting_id}/transcript")
def put_transcript(ws: WS, meeting_id: str, body: TranscriptIn) -> dict[str, Any]:
    if len(body.text.encode("utf-8")) > ingest.MAX_TEXT_BYTES:
        raise HTTPException(413, "Transcript too large")
    try:
        services.set_transcript(
            ws,
            meeting_id,
            body.text,
            source="correction" if body.reason else "paste",
            reason=body.reason,
        )
    except services.ServiceError as e:
        raise _err(e) from e
    return get_meeting(ws, meeting_id)


@router.post("/w/{workspace_id}/meetings/{meeting_id}/transcript/upload")
async def upload_transcript(ws: WS, meeting_id: str, file: UploadFile = File(...)) -> dict[str, Any]:  # noqa: B008
    data = await file.read()
    name = (file.filename or "").lower()
    if not name.endswith((".txt", ".md", ".markdown")):
        raise HTTPException(400, "Upload a .txt or .md transcript")
    if len(data) > ingest.MAX_TEXT_BYTES:
        raise HTTPException(413, "Transcript too large")
    try:
        services.set_transcript(ws, meeting_id, data.decode("utf-8", errors="replace"), source="upload")
    except services.ServiceError as e:
        raise _err(e) from e
    return get_meeting(ws, meeting_id)


@router.get("/w/{workspace_id}/meetings/{meeting_id}/transcript")
def get_transcript(ws: WS, meeting_id: str, version: int | None = None) -> dict[str, Any]:
    with session_for(ws) as s:
        q = select(TranscriptVersion).where(TranscriptVersion.meeting_id == meeting_id)
        q = (
            q.where(TranscriptVersion.version_no == version)
            if version
            else q.order_by(TranscriptVersion.version_no.desc())
        )
        tv = s.scalars(q).first()
        if tv is None:
            raise HTTPException(404, "no transcript")
        versions = [
            {
                "id": v.id,
                "version_no": v.version_no,
                "source": v.source,
                "reason": v.reason,
                "created_at": v.created_at,
            }
            for v in s.scalars(
                select(TranscriptVersion)
                .where(TranscriptVersion.meeting_id == meeting_id)
                .order_by(TranscriptVersion.version_no)
            )
        ]
        return {**_dump(tv), "versions": versions}


# ------------------------------------------------------------------ processing jobs


def _process_runner(ws: Workspace, job: Job, report: Any, should_cancel: Any) -> None:
    assert job.meeting_id
    services.process_meeting(ws, job.meeting_id, progress=report, should_cancel=should_cancel)


jobs.register("process", _process_runner)


@router.post("/w/{workspace_id}/meetings/{meeting_id}/process", status_code=202)
def start_processing(ws: WS, meeting_id: str) -> dict[str, Any]:
    with session_for(ws) as s:
        m = services.get_meeting(s, meeting_id)
        if services.latest_transcript(s, meeting_id) is None:
            raise HTTPException(400, "Add a transcript first.")
        appr = services.current_agenda(s, meeting_id, approved_only=True)
        cur = services.current_agenda(s, meeting_id)
        if appr is None or cur is None or appr.id != cur.id:
            raise HTTPException(400, "Approve the agenda first.")
        if m.state == "approved":
            raise HTTPException(400, "Meeting is approved; reopen it to regenerate.")
    if not config.load_settings().models.primary_model:
        raise HTTPException(400, "Choose a primary model in Settings first.")
    return _dump(jobs.enqueue(ws, "process", meeting_id))


@router.get("/w/{workspace_id}/jobs/{job_id}")
def get_job(ws: WS, job_id: str) -> dict[str, Any]:
    with session_for(ws) as s:
        j = s.get(Job, job_id)
        if j is None:
            raise HTTPException(404, "job not found")
        return _dump(j)


@router.get("/w/{workspace_id}/meetings/{meeting_id}/jobs")
def meeting_jobs(ws: WS, meeting_id: str) -> list[dict[str, Any]]:
    with session_for(ws) as s:
        return [
            _dump(j)
            for j in s.scalars(
                select(Job).where(Job.meeting_id == meeting_id).order_by(Job.created_at.desc()).limit(10)
            )
        ]


@router.post("/w/{workspace_id}/jobs/{job_id}/cancel")
def cancel_job(ws: WS, job_id: str) -> dict[str, Any]:
    j = jobs.cancel(ws, job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    return _dump(j)


# ------------------------------------------------------------------ notes & review


class NoteIn(BaseModel):
    text: str | None = None
    agenda_node_id: str | None = Field(default="__unset__")
    follow_up: bool | None = None
    owner: str | None = Field(default="__unset__")
    deleted: bool | None = None
    position: int | None = None
    evidence_quotes: list[str] | None = None


@router.get("/w/{workspace_id}/meetings/{meeting_id}/review")
def review_view(ws: WS, meeting_id: str) -> dict[str, Any]:
    with session_for(ws) as s:
        m = services.get_meeting(s, meeting_id)
        notes = [_dump(n) for n in s.scalars(select(Note).where(Note.meeting_id == meeting_id).order_by(Note.position))]
        findings = [
            _dump(f)
            for f in s.scalars(
                select(ReviewFinding)
                .where(ReviewFinding.meeting_id == meeting_id)
                .order_by(ReviewFinding.round_no, ReviewFinding.created_at)
            )
        ]
        out: dict[str, Any] = {
            "meeting": _meeting_out(s, m),
            "notes": notes,
            "findings": findings,
            "coverage": m.coverage,
        }
    out["markdown"] = services.render_meeting(ws, meeting_id)
    out["undiscussed_follow_ups"] = services.undiscussed_agenda_follow_ups(ws, meeting_id)
    return out


@router.post("/w/{workspace_id}/meetings/{meeting_id}/notes", status_code=201)
def create_note(ws: WS, meeting_id: str, body: NoteIn) -> dict[str, Any]:
    return _edit_note(ws, meeting_id, None, body)


@router.patch("/w/{workspace_id}/meetings/{meeting_id}/notes/{note_id}")
def patch_note(ws: WS, meeting_id: str, note_id: str, body: NoteIn) -> dict[str, Any]:
    return _edit_note(ws, meeting_id, note_id, body)


def _edit_note(ws: Workspace, meeting_id: str, note_id: str | None, body: NoteIn) -> dict[str, Any]:
    data = {k: v for k, v in body.model_dump().items() if v is not None and v != "__unset__"}
    if body.agenda_node_id is None:
        data["agenda_node_id"] = None
    if body.owner is None:
        data["owner"] = None
    if note_id is None and not data.get("text"):
        raise HTTPException(400, "text is required")
    try:
        n = services.edit_note(ws, meeting_id, note_id, data)
        services.revalidate_notes(ws, meeting_id)
    except services.ServiceError as e:
        raise _err(e) from e
    return _dump(n)


@router.post("/w/{workspace_id}/meetings/{meeting_id}/findings/{finding_id}/resolve")
def resolve_finding(ws: WS, meeting_id: str, finding_id: str, body: dict[str, bool] | None = None) -> dict[str, str]:
    try:
        services.resolve_finding(ws, meeting_id, finding_id, (body or {}).get("resolved", True))
    except services.ServiceError as e:
        raise _err(e) from e
    return {"ok": "true"}


@router.post("/w/{workspace_id}/meetings/{meeting_id}/approve")
def approve_notes(ws: WS, meeting_id: str) -> dict[str, Any]:
    try:
        rev = services.approve_notes(ws, meeting_id)
    except services.ServiceError as e:
        raise _err(e) from e
    return _dump(rev)


@router.get("/w/{workspace_id}/meetings/{meeting_id}/notes/revisions")
def notes_revisions(ws: WS, meeting_id: str) -> list[dict[str, Any]]:
    with session_for(ws) as s:
        return [
            _dump(r)
            for r in s.scalars(
                select(NotesRevision).where(NotesRevision.meeting_id == meeting_id).order_by(NotesRevision.revision_no)
            )
        ]


@router.get("/w/{workspace_id}/meetings/{meeting_id}/export")
def export_notes(ws: WS, meeting_id: str, fmt: str = Query("md", pattern="^(md|txt)$")) -> Any:
    from fastapi.responses import PlainTextResponse

    from .render import to_plain_text

    with session_for(ws) as s:
        m = services.get_meeting(s, meeting_id)
        rev = s.scalars(
            select(NotesRevision)
            .where(NotesRevision.meeting_id == meeting_id)
            .order_by(NotesRevision.revision_no.desc())
        ).first()
        md = rev.markdown if (rev and m.state == "approved") else None
    if md is None:
        md = services.render_meeting(ws, meeting_id)
    body = md if fmt == "md" else to_plain_text(md)
    fname = f"{ws.id}_{m.meeting_date or 'undated'}_{meeting_id[:8]}.{fmt}"
    return PlainTextResponse(
        body,
        media_type="text/markdown" if fmt == "md" else "text/plain",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ------------------------------------------------------------------ history


@router.get("/w/{workspace_id}/follow-ups")
def follow_ups(ws: WS, status: str | None = None) -> list[dict[str, Any]]:
    return [{**_dump(f), "events": [_dump(e) for e in f.events]} for f in history.list_follow_ups(ws, status)]


@router.post("/w/{workspace_id}/follow-ups", status_code=201)
def create_follow_up(ws: WS, body: dict[str, Any]) -> dict[str, Any]:
    if not str(body.get("description", "")).strip():
        raise HTTPException(400, "description required")
    f = history.create_follow_up(ws, body)
    return {**_dump(f), "events": [_dump(e) for e in f.events]}


@router.patch("/w/{workspace_id}/follow-ups/{follow_up_id}")
def patch_follow_up(ws: WS, follow_up_id: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        f = history.update_follow_up(ws, follow_up_id, body)
    except services.ServiceError as e:
        raise _err(e) from e
    return {**_dump(f), "events": [_dump(e) for e in f.events]}


@router.get("/w/{workspace_id}/decisions")
def decisions(ws: WS) -> list[dict[str, Any]]:
    return [_dump(d) for d in history.list_decisions(ws)]


@router.patch("/w/{workspace_id}/decisions/{decision_id}")
def patch_decision(ws: WS, decision_id: str, body: dict[str, str]) -> dict[str, Any]:
    try:
        return _dump(history.update_decision(ws, decision_id, body.get("status", "")))
    except services.ServiceError as e:
        raise _err(e) from e


@router.get("/w/{workspace_id}/calling-events")
def calling_events(ws: WS) -> dict[str, Any]:
    return {"types": CALLING_EVENTS, "events": [_dump(c) for c in history.list_calling_events(ws)]}


@router.post("/w/{workspace_id}/calling-events", status_code=201)
def add_calling_event(ws: WS, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return _dump(history.add_calling_event(ws, body))
    except services.ServiceError as e:
        raise _err(e) from e


@router.patch("/w/{workspace_id}/calling-events/{event_id}")
def patch_calling_event(ws: WS, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return _dump(history.update_calling_event(ws, event_id, body))
    except services.ServiceError as e:
        raise _err(e) from e


@router.get("/w/{workspace_id}/events")
def scheduled_events(ws: WS) -> list[dict[str, Any]]:
    return [_dump(e) for e in history.list_scheduled_events(ws)]


@router.post("/w/{workspace_id}/events/{event_id}/date")
def set_event_date(ws: WS, event_id: str, body: dict[str, str]) -> dict[str, Any]:
    try:
        return _dump(history.set_event_date(ws, event_id, body.get("event_date", ""), body.get("note")))
    except services.ServiceError as e:
        raise _err(e) from e


@router.get("/w/{workspace_id}/audit")
def audit_log(ws: WS, meeting_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    return [_dump(a) for a in history.list_audit(ws, limit, meeting_id)]


@router.get("/w/{workspace_id}/search")
def search(
    ws: WS,
    q: str,
    entity_type: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    return search_mod.search(ws, q, entity_type=entity_type, status=status, date_from=date_from, date_to=date_to)


@router.post("/w/{workspace_id}/search/reindex")
def reindex(ws: WS) -> dict[str, int]:
    return {"meetings": search_mod.reindex_all(ws)}


# ------------------------------------------------------------------ reference documents


@router.get("/w/{workspace_id}/references")
def references(ws: WS) -> list[dict[str, Any]]:
    with session_for(ws) as s:
        return [
            {k: v for k, v in _dump(r).items() if k != "text"} | {"length": len(r.text)}
            for r in s.scalars(select(ReferenceDocument).order_by(ReferenceDocument.created_at.desc()))
        ]


@router.post("/w/{workspace_id}/references", status_code=201)
async def upload_reference(ws: WS, file: UploadFile = File(...)) -> dict[str, Any]:  # noqa: B008
    data = await file.read()
    try:
        text, kind = ingest.extract_document_text(file.filename or "", data)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    with session_for(ws) as s:
        doc = ReferenceDocument(
            filename=file.filename or "document", kind=kind, sha256=services.sha256(data), text=text
        )
        s.add(doc)
        s.flush()
        ws.file_path(ws.references, f"{doc.id}_{file.filename or 'doc'}").write_bytes(data)
        s.commit()
        return {k: v for k, v in _dump(doc).items() if k != "text"} | {"length": len(text)}


@router.get("/w/{workspace_id}/references/{doc_id}")
def get_reference(ws: WS, doc_id: str) -> dict[str, Any]:
    with session_for(ws) as s:
        d = s.get(ReferenceDocument, doc_id)
        if d is None:
            raise HTTPException(404, "not found")
        return _dump(d)


@router.delete("/w/{workspace_id}/references/{doc_id}", status_code=204)
def delete_reference(ws: WS, doc_id: str) -> None:
    with session_for(ws) as s:
        d = s.get(ReferenceDocument, doc_id)
        if d is None:
            raise HTTPException(404, "not found")
        for f in ws.references.glob(f"{doc_id}_*"):
            f.unlink(missing_ok=True)
        s.delete(d)
        s.commit()


@router.get("/w/{workspace_id}/meetings/{meeting_id}/glossary-suggestions")
def glossary_suggestions(ws: WS, meeting_id: str) -> list[dict[str, Any]]:
    """Spelling suggestions from glossary reference docs. Advisory only; never applied automatically."""
    import difflib
    import re

    with session_for(ws) as s:
        terms: set[str] = set()
        for d in s.scalars(select(ReferenceDocument).where(ReferenceDocument.use_as_glossary.is_(True))):
            terms.update(t for t in re.findall(r"\b[A-Z][a-z]+(?: [A-Z][a-z]+)*\b", d.text) if len(t) > 3)
        if not terms:
            return []
        out = []
        for n in s.scalars(select(Note).where(Note.meeting_id == meeting_id, Note.deleted.is_(False))):
            for word in set(re.findall(r"\b[A-Z][a-z]{3,}\b", n.text)):
                if word in terms:
                    continue
                close = difflib.get_close_matches(word, terms, n=1, cutoff=0.85)
                if close:
                    out.append(
                        {
                            "note_id": n.id,
                            "found": word,
                            "suggestion": close[0],
                            "requires_confirmation": True,
                        }
                    )
        return out


@router.post("/w/{workspace_id}/references/{doc_id}/glossary")
def toggle_glossary(ws: WS, doc_id: str, body: dict[str, bool]) -> dict[str, Any]:
    with session_for(ws) as s:
        d = s.get(ReferenceDocument, doc_id)
        if d is None:
            raise HTTPException(404, "not found")
        d.use_as_glossary = bool(body.get("use_as_glossary", False))
        s.commit()
        return {k: v for k, v in _dump(d).items() if k != "text"}
