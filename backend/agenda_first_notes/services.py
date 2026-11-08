"""Application services: the only place state changes happen. Every function takes a Workspace."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import date
from typing import Any

from sqlalchemy import delete, null, or_, select, update
from sqlalchemy import text as sql
from sqlalchemy.orm import Session

from . import config, evidence, pipeline, render
from .agenda import AgendaNode, from_dicts, node_ids, parse_text, to_dicts, validate_tree, walk
from .llm import get_provider
from .llm.base import ModelProvider, ProviderError
from .models import (
    TRANSITIONS,
    AgendaRevision,
    AuditEvent,
    CallingEvent,
    Decision,
    FollowUp,
    FollowUpEvent,
    Job,
    Meeting,
    Note,
    NotesRevision,
    ReviewFinding,
    ScheduledEvent,
    TranscriptVersion,
)
from .segmentation import Segment, coverage_complete, segment
from .workspaces import Workspace, engine_for, session_for

UNSET = "__unset__"  # sentinel: "field not supplied" (None means "clear it")


class ServiceError(ValueError):
    pass


class NotFound(ServiceError):
    pass


class Cancelled(RuntimeError):
    pass


def sha256(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


def audit(
    s: Session,
    entity_type: str,
    entity_id: str,
    action: str,
    *,
    prev: dict | None = None,
    new: dict | None = None,
    source: str,
    meeting_id: str | None = None,
) -> None:
    s.add(
        AuditEvent(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            prev=prev,
            new=new,
            source=source,
            meeting_id=meeting_id,
        )
    )


# ---------------------------------------------------------------- meetings


def transition(m: Meeting, new_state: str) -> None:
    if new_state not in TRANSITIONS.get(m.state, set()) and new_state != m.state:
        raise ServiceError(f"Cannot move meeting from {m.state} to {new_state}")
    m.state = new_state


def get_meeting(s: Session, meeting_id: str) -> Meeting:
    m = s.get(Meeting, meeting_id)
    if m is None:
        raise NotFound("meeting not found")
    return m


def _not_processing(m: Meeting) -> None:
    """The running job owns the meeting's notes, agenda and transcript until it finishes or is cancelled."""
    if m.state == "processing":
        raise ServiceError("Notes are being generated; wait for the job to finish or cancel it first.")


def create_meeting(ws: Workspace, title: str, meeting_date: str | None) -> Meeting:
    with session_for(ws) as s:
        m = Meeting(title=title.strip()[:200], meeting_date=_validate_date(meeting_date))
        s.add(m)
        s.commit()
        return m


def _validate_date(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(value).isoformat()  # type: ignore[arg-type]
    except ValueError as e:
        raise ServiceError("meeting_date must be an ISO date (YYYY-MM-DD)") from e


def update_meeting(
    ws: Workspace,
    meeting_id: str,
    *,
    title: str | None = None,
    meeting_date: str | None = UNSET,
    carry_forward: list[str] | None = None,
) -> Meeting:
    with session_for(ws) as s:
        m = get_meeting(s, meeting_id)
        ticked = list(m.carry_forward or [])
        prev = {"title": m.title, "meeting_date": m.meeting_date, "carry_forward": ticked}
        if title is not None:
            m.title = title.strip()[:200]
        if meeting_date is not UNSET:
            m.meeting_date = _validate_date(meeting_date)
        if carry_forward is not None:
            allowed = {c["follow_up_id"] for c in _undiscussed(s, m)}
            wanted = [str(x) for x in carry_forward]
            # Newly ticked items must qualify; ticked items that stopped qualifying are dropped so they can be unticked.
            if any(x not in allowed and x not in ticked for x in wanted):
                raise ServiceError(
                    "Only open follow-ups listed on this agenda and not discussed can be carried forward."
                )
            m.carry_forward = [x for x in wanted if x in allowed][:200]
        new = {"title": m.title, "meeting_date": m.meeting_date, "carry_forward": list(m.carry_forward or [])}
        if new != prev:
            if m.state == "approved":
                raise ServiceError("Approved meeting; reopen it before changing it.")
            audit(s, "meeting", meeting_id, "updated", prev=prev, new=new, source="user", meeting_id=meeting_id)
        s.commit()
        return m


def delete_meeting(ws: Workspace, meeting_id: str) -> None:
    """A deleted meeting is really gone: its content, history it alone owns, audit payloads and index rows."""
    with session_for(ws) as s:
        m = get_meeting(s, meeting_id)
        _not_processing(m)
        removed: dict[str, list[str]] = {}
        for table in (ReviewFinding, NotesRevision, Decision, CallingEvent, ScheduledEvent, Job):
            ids = list(s.scalars(select(table.id).where(table.meeting_id == meeting_id)))
            if ids:
                removed[str(table.__tablename__)] = ids
            s.execute(delete(table).where(table.meeting_id == meeting_id))
        for fu in s.scalars(select(FollowUp).where(FollowUp.meeting_id == meeting_id)):
            if any(e.meeting_id != meeting_id for e in fu.events):
                # Tracked beyond this meeting: keep the item, drop every link to and quote from the deleted meeting.
                fu.meeting_id = fu.source_note_id = None
                fu.evidence = []
                for e in fu.events:
                    if e.meeting_id == meeting_id:
                        e.meeting_id, e.evidence = None, []
            else:
                removed.setdefault("follow_ups", []).append(fu.id)
                s.delete(fu)
        gone = set(removed.get("follow_ups", []))
        for other in s.scalars(select(Meeting).where(Meeting.id != meeting_id)):
            if gone & set(other.carry_forward or []):
                other.carry_forward = [x for x in other.carry_forward if x not in gone]
        # Deliberate privacy exception to the append-only audit log: a deleted meeting's payloads are redacted.
        s.execute(update(AuditEvent).where(AuditEvent.meeting_id == meeting_id).values(prev=null(), new=null()))
        s.execute(sql("DELETE FROM search_index WHERE meeting_id = :m"), {"m": meeting_id})
        s.execute(sql("INSERT INTO search_index(search_index) VALUES('optimize')"))  # drop deleted FTS tokens
        audit(s, "meeting", meeting_id, "deleted", prev={"history_rows": removed}, source="user")  # ids only
        s.delete(m)
        s.commit()
    with engine_for(ws).connect() as conn:  # secure_delete zeroes freed pages; move them out of the WAL too
        conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    for f in ws.uploads.glob(f"{meeting_id}_*"):
        f.unlink(missing_ok=True)


# ---------------------------------------------------------------- agenda


def current_agenda(s: Session, meeting_id: str, approved_only: bool = False) -> AgendaRevision | None:
    q = select(AgendaRevision).where(AgendaRevision.meeting_id == meeting_id)
    if approved_only:
        q = q.where(AgendaRevision.approved_at.is_not(None))
    return s.scalars(q.order_by(AgendaRevision.revision_no.desc())).first()


def set_agenda(
    ws: Workspace,
    meeting_id: str,
    tree: list[AgendaNode] | None = None,
    *,
    text: str | None = None,
    source: str,
) -> AgendaRevision:
    """Create a new (unapproved) agenda revision. Approved revisions are immutable."""
    if tree is None:
        tree = parse_text(text or "")
    validate_tree(tree)
    with session_for(ws) as s:
        m = get_meeting(s, meeting_id)
        if m.state == "approved":
            raise ServiceError("Approved meeting; reopen it before changing the agenda.")
        _not_processing(m)
        prev = current_agenda(s, meeting_id)
        rev = AgendaRevision(
            meeting_id=meeting_id,
            revision_no=(prev.revision_no + 1 if prev else 1),
            tree=to_dicts(tree),
            source=source,
        )
        s.add(rev)
        transition(m, "agenda_imported")
        s.commit()
        return rev


def _mark_notes_stale_for_removed_nodes(s: Session, meeting_id: str, valid_ids: set[str]) -> None:
    for n in s.scalars(select(Note).where(Note.meeting_id == meeting_id, Note.deleted.is_(False))):
        if n.agenda_node_id and n.agenda_node_id not in valid_ids:
            n.agenda_node_id = None
            n.status = "needs_review"


def approve_agenda(ws: Workspace, meeting_id: str, revision_id: str | None = None) -> AgendaRevision:
    from .models import now

    with session_for(ws) as s:
        m = get_meeting(s, meeting_id)
        rev = s.get(AgendaRevision, revision_id) if revision_id else current_agenda(s, meeting_id)
        if rev is None or rev.meeting_id != meeting_id:
            raise NotFound("agenda revision not found")
        if not rev.tree:
            raise ServiceError("Agenda is empty")
        _not_processing(m)
        if rev.approved_at is None:
            rev.approved_at = now()
            # The notes were generated against another tree: detach notes from removed nodes and forget coverage,
            # so nothing claims "Not discussed." for items that were never processed.
            _mark_notes_stale_for_removed_nodes(s, meeting_id, node_ids(from_dicts(rev.tree)))
            m.coverage = None
        has_transcript = latest_transcript(s, meeting_id) is not None
        transition(m, "transcript_added" if has_transcript else "agenda_verified")
        s.commit()
        return rev


# ---------------------------------------------------------------- transcript


def latest_transcript(s: Session, meeting_id: str) -> TranscriptVersion | None:
    return s.scalars(
        select(TranscriptVersion)
        .where(TranscriptVersion.meeting_id == meeting_id)
        .order_by(TranscriptVersion.version_no.desc())
    ).first()


def set_transcript(
    ws: Workspace, meeting_id: str, text: str, *, source: str, reason: str | None = None
) -> TranscriptVersion:
    """Store a transcript version. Corrections create a new version linked to the previous one."""
    if not text.strip():
        raise ServiceError("Transcript is empty")
    with session_for(ws) as s:
        m = get_meeting(s, meeting_id)
        if m.state == "approved":
            raise ServiceError("Approved meeting; reopen it before changing the transcript.")
        _not_processing(m)
        prev = latest_transcript(s, meeting_id)
        if prev and prev.text == text:
            return prev
        tv = TranscriptVersion(
            meeting_id=meeting_id,
            version_no=(prev.version_no + 1 if prev else 1),
            text=text,
            sha256=sha256(text),
            source=source,
            reason=reason,
            parent_id=prev.id if prev else None,
        )
        s.add(tv)
        s.flush()
        if prev is not None:
            # Source changed: re-locate evidence; anything that no longer matches is flagged stale.
            for n in s.scalars(select(Note).where(Note.meeting_id == meeting_id, Note.deleted.is_(False))):
                if n.origin == "user" and not n.evidence:
                    continue
                before = len(n.evidence)
                n.evidence = evidence.revalidate(n.evidence, text, tv.id)
                if len(n.evidence) < before or not n.evidence:
                    n.stale = True
                    n.status = "needs_review"
            m.coverage = None
        if m.state in (
            "ready_for_approval",
            "review_required",
            "failed",
            "processing",
        ) or current_agenda(s, meeting_id, approved_only=True):
            transition(m, "transcript_added")
        elif m.state in ("draft", "agenda_imported"):
            transition(m, "transcript_added")
        s.commit()
        return tv


# ---------------------------------------------------------------- processing


def _notes_as_dicts(notes: list[Note]) -> list[dict[str, Any]]:
    return [
        {
            "id": n.id,
            "agenda_node_id": n.agenda_node_id,
            "position": n.position,
            "text": n.text,
            "category": n.category,
            "uncertainty": n.uncertainty,
            "follow_up": n.follow_up,
            "owner": n.owner,
            "calling_event": n.calling_event,
            "status": n.status,
            "origin": n.origin,
            "evidence": list(n.evidence),
            "stale": n.stale,
            "deleted": n.deleted,
            "source_text": n.source_text,
            "user_content": n.user_content,
        }
        for n in notes
    ]


def process_meeting(
    ws: Workspace,
    meeting_id: str,
    *,
    progress: Callable[[float, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    provider: ModelProvider | None = None,
) -> dict[str, Any]:
    """Run extraction + review for a meeting. Idempotent: re-running replaces model notes, keeps user edits."""
    provider = provider or get_provider()
    settings = config.load_settings().models
    if not settings.primary_model:
        raise ServiceError("No primary model selected. Choose one in Settings.")
    report = progress or (lambda _p, _m: None)
    cancel = should_cancel or (lambda: False)

    with session_for(ws) as s:
        m = get_meeting(s, meeting_id)
        tv = latest_transcript(s, meeting_id)
        if tv is None:
            raise ServiceError("Add a transcript first.")
        rev = current_agenda(s, meeting_id, approved_only=True)
        if rev is None or rev.id != (current_agenda(s, meeting_id) or rev).id:
            raise ServiceError("Approve the agenda before generating notes.")
        tree = from_dicts(rev.tree)
        transition(m, "processing")
        m.last_error = None
        # Replace previous model-origin notes; user notes survive and are re-checked against the agenda.
        for n in s.scalars(select(Note).where(Note.meeting_id == meeting_id)):
            if n.origin == "model":
                s.delete(n)
        for f in s.scalars(select(ReviewFinding).where(ReviewFinding.meeting_id == meeting_id)):
            s.delete(f)
        segs = segment(tv.text, settings.segment_chars, settings.segment_overlap_chars)
        m.coverage = {
            "segments": [x.model_dump() for x in segs],
            "complete": False,
            "prompt_version": pipeline.prompts.PROMPT_VERSION,
            "model": settings.primary_model,
        }
        s.commit()
        text, tv_id, wid = tv.text, tv.id, ws.id

    all_notes: list[dict[str, Any]] = []
    all_findings: list[dict[str, Any]] = []
    try:
        for seg in segs:
            if cancel():
                raise Cancelled()
            report(
                0.1 + 0.6 * seg.index / max(len(segs), 1),
                f"Extracting segment {seg.index + 1} of {len(segs)}",
            )
            try:
                notes, findings = pipeline.extract_segment(provider, settings, wid, tree, text, seg, len(segs), tv_id)
                seg.status = "done"
                all_notes.extend(notes)
                all_findings.extend(findings)
            except (pipeline.PipelineError, ProviderError) as e:
                seg.status = "failed"
                seg.error = str(e)[:1000]
                all_findings.append(
                    {
                        "note_id": None,
                        "severity": "high",
                        "category": "segment_failed",
                        "message": f"Segment {seg.index + 1} of {len(segs)} (characters {seg.start}-{seg.end}) was not processed: {seg.error}",
                        "action": None,
                        "source": "deterministic",
                    }
                )
            _save_coverage(ws, meeting_id, segs)
        notes = pipeline.dedupe(all_notes)
        with session_for(ws) as s:
            # Any user-touched note (edited, untagged, moved or deleted) wins over a fresh model note making the
            # same claim (its current or its original model text) anywhere in the meeting; otherwise regeneration
            # would resurrect a moved/edited note or duplicate it.
            suppressed = [
                t
                for x in s.scalars(select(Note).where(Note.meeting_id == meeting_id, Note.origin == "user"))
                for t in (x.text, x.source_text)
                if t
            ]
        notes = [n for n in notes if not any(evidence.same_claim(n["text"], t) for t in suppressed)]
        for i, nd in enumerate(notes):
            nd["position"] = i
        with session_for(ws) as s:
            m = get_meeting(s, meeting_id)
            user_notes = list(s.scalars(select(Note).where(Note.meeting_id == meeting_id)))
            for nd in notes:
                row = Note(**{k: v for k, v in nd.items() if not k.startswith("_")}, meeting_id=meeting_id)
                s.add(row)
                s.flush()
                nd["id"] = row.id
                for fd in nd.get("_findings", []):
                    fd["note_id"] = row.id
            for fd in all_findings:
                s.add(
                    ReviewFinding(
                        meeting_id=meeting_id,
                        round_no=0,
                        **{k: v for k, v in fd.items() if k != "round_no"},
                    )
                )
            s.commit()
            notes = _notes_as_dicts(user_notes) + notes
        report(0.75, "Reviewing notes")
        if cancel():
            raise Cancelled()
        converged = run_review(
            ws,
            meeting_id,
            notes,
            provider=provider,
            settings=settings,
            tree=tree,
            text=text,
            tv_id=tv_id,
            report=report,
            cancel=cancel,
        )
        with session_for(ws) as s:
            m = get_meeting(s, meeting_id)
            complete = coverage_complete(segs)
            m.coverage = {
                **(m.coverage or {}),
                "segments": [x.model_dump() for x in segs],
                "complete": complete,
            }
            transition(m, "ready_for_approval" if converged and complete else "review_required")
            s.commit()
            report(1.0, "Done" if converged and complete else "Review required")
            return {"state": m.state, "coverage_complete": complete, "converged": converged}
    except Cancelled:
        with session_for(ws) as s:
            mc = s.get(Meeting, meeting_id)
            if mc is not None and mc.state == "processing":
                for x in segs:
                    if x.status == "pending":
                        x.status = "cancelled"
                mc.coverage = {
                    **(mc.coverage or {}),
                    "segments": [x.model_dump() for x in segs],
                    "complete": False,
                }
                transition(mc, "transcript_added")
                s.commit()
        raise
    except Exception as e:
        # Must not raise itself: the original error is what the job records.
        with session_for(ws) as s:
            mf = s.get(Meeting, meeting_id)
            if mf is not None:
                mf.last_error = str(e)[:2000]
                if mf.state == "processing":
                    transition(mf, "failed")
                s.commit()
        raise


def _save_coverage(ws: Workspace, meeting_id: str, segs: list[Segment]) -> None:
    with session_for(ws) as s:
        m = get_meeting(s, meeting_id)
        m.coverage = {**(m.coverage or {}), "segments": [x.model_dump() for x in segs]}
        s.commit()


def run_review(
    ws: Workspace,
    meeting_id: str,
    notes: list[dict[str, Any]],
    *,
    provider: ModelProvider,
    settings: config.ModelSettings,
    tree: list[AgendaNode],
    text: str,
    tv_id: str,
    report: Callable[[float, str], None],
    cancel: Callable[[], bool],
) -> bool:
    """Adversarial review loop. Returns True when technically converged."""
    rounds = settings.max_review_rounds
    material_left = 0
    for r in range(1, rounds + 1):
        if cancel():
            raise Cancelled()
        report(0.75 + 0.2 * (r - 1) / max(rounds, 1), f"Adversarial review round {r} of {rounds}")
        try:
            notes, findings, material = pipeline.review_round(provider, settings, ws.id, tree, notes, text, tv_id, r)
        except (pipeline.PipelineError, ProviderError) as e:
            findings, material = (
                [
                    {
                        "note_id": None,
                        "severity": "high",
                        "category": "review_failed",
                        "message": str(e),
                        "action": None,
                        "source": "model",
                        "round_no": r,
                    }
                ],
                1,
            )
        det = pipeline.deterministic_checks(_checked(notes), tree)
        _persist_review(ws, meeting_id, notes, findings, det, r)
        material_left = material + sum(1 for d in det if d["severity"] == "high")
        if material_left == 0:
            return True
    if rounds == 0:
        det = pipeline.deterministic_checks(_checked(notes), tree)
        _persist_review(ws, meeting_id, notes, [], det, 0)
        return not any(d["severity"] == "high" for d in det)
    return False


def _checked(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Notes the user only moved, retagged or reordered still make the model's claim: check them as model notes."""
    return [{**n, "origin": "model"} if n["origin"] == "user" and not n.get("user_content") else n for n in notes]


def _persist_review(
    ws: Workspace,
    meeting_id: str,
    notes: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    det: list[dict[str, Any]],
    round_no: int,
) -> None:
    with session_for(ws) as s:
        for n in notes:
            if "id" in n:
                row = s.get(Note, n["id"])
                if row is None or row.origin == "user":
                    continue  # the review never changes user notes; the row may also be newer than this copy
                for k in (
                    "agenda_node_id",
                    "text",
                    "uncertainty",
                    "follow_up",
                    "owner",
                    "status",
                    "deleted",
                    "evidence",
                    "position",
                ):
                    setattr(row, k, n[k])
            else:
                row = Note(**{k: v for k, v in n.items() if not k.startswith("_")}, meeting_id=meeting_id)
                s.add(row)
                s.flush()
                n["id"] = row.id
        for f in findings + [{**d, "round_no": round_no} for d in det]:
            s.add(ReviewFinding(meeting_id=meeting_id, **{k: v for k, v in f.items()}))
        s.commit()


def revalidate_notes(ws: Workspace, meeting_id: str) -> list[dict[str, Any]]:
    """Deterministic re-check only (no model). Used after manual edits."""
    with session_for(ws) as s:
        get_meeting(s, meeting_id)
        rev = current_agenda(s, meeting_id, approved_only=True)
        tree = from_dicts(rev.tree) if rev else []
        notes = _notes_as_dicts(list(s.scalars(select(Note).where(Note.meeting_id == meeting_id))))
        det = pipeline.deterministic_checks(_checked(notes), tree)
        dismissed = set()
        for f in s.scalars(select(ReviewFinding).where(ReviewFinding.meeting_id == meeting_id)):
            if f.resolved:
                dismissed.add((f.note_id, f.category))
            if f.source == "deterministic" and f.round_no == 99:
                s.delete(f)
        for d in det:
            # A finding the user already dismissed for this note stays dismissed when the checks re-run.
            resolved = (d["note_id"], d["category"]) in dismissed
            s.add(ReviewFinding(meeting_id=meeting_id, round_no=99, resolved=resolved, **d))
        s.commit()
        return det


# ---------------------------------------------------------------- notes editing


def edit_note(ws: Workspace, meeting_id: str, note_id: str | None, data: dict[str, Any]) -> Note:
    """Create or edit a note. User text without matching evidence is 'manual_unverified', never 'verified'."""
    with session_for(ws) as s:
        m = get_meeting(s, meeting_id)
        if m.state == "approved":
            raise ServiceError("Approved meeting; reopen it before editing notes.")
        _not_processing(m)
        tv = latest_transcript(s, meeting_id)
        rev = current_agenda(s, meeting_id, approved_only=True)
        valid_ids = node_ids(from_dicts(rev.tree)) if rev else set()
        if note_id:
            n = s.get(Note, note_id)
            if n is None or n.meeting_id != meeting_id:
                raise NotFound("note not found")
            prev = {
                "text": n.text,
                "status": n.status,
                "owner": n.owner,
                "follow_up": n.follow_up,
                "agenda_node_id": n.agenda_node_id,
            }
            if n.origin == "model" and n.source_text is None:
                n.source_text = n.text  # lets regeneration recognise this claim after the user moves or rewords it
        else:
            n = Note(meeting_id=meeting_id, position=10_000, evidence=[])
            s.add(n)
            prev = None
        if "agenda_node_id" in data:
            if data["agenda_node_id"] is not None and data["agenda_node_id"] not in valid_ids:
                raise ServiceError("agenda_node_id is not in the approved agenda")
            n.agenda_node_id = data["agenda_node_id"]
        if "deleted" in data:
            n.deleted = bool(data["deleted"])
        if "follow_up" in data:
            n.follow_up = bool(data["follow_up"])
        old_owner = n.owner
        if "owner" in data:
            n.owner = " ".join((data["owner"] or "").split())[:120] or None
        if "position" in data:
            n.position = int(data["position"])
        text_changed = "text" in data and data["text"].strip() != (n.text or "")
        if text_changed:
            n.text = data["text"].strip()[:2000]
            if not n.text:
                raise ServiceError("note text cannot be empty")
        if "evidence_quotes" in data and tv is not None:
            ev, _bad = evidence.validate_quotes(list(data["evidence_quotes"]), tv.text, 0, tv.id)
            n.evidence = ev
        n.origin = "user"  # any edit (delete, move, untag included) is a user decision regeneration must respect
        if text_changed or not note_id or "evidence_quotes" in data or n.owner != old_owner:
            n.user_content = True  # the note now carries the user's claim, not the transcript's
            n.stale = False
            owner_ok = evidence.owner_supported(n.owner, n.evidence)
            n.status = "user_edited" if (n.evidence and owner_ok) else "manual_unverified"
        elif n.status == "verified":
            n.status = "user_edited"  # user edits are never labelled 'verified'
        s.flush()
        audit(
            s,
            "note",
            n.id,
            "edited",
            prev=prev,
            new={
                "text": n.text,
                "status": n.status,
                "owner": n.owner,
                "follow_up": n.follow_up,
                "agenda_node_id": n.agenda_node_id,
            },
            source="user",
            meeting_id=meeting_id,
        )
        s.commit()
        s.refresh(n)
        return n


def resolve_finding(ws: Workspace, meeting_id: str, finding_id: str, resolved: bool = True) -> None:
    with session_for(ws) as s:
        f = s.get(ReviewFinding, finding_id)
        if f is None or f.meeting_id != meeting_id:
            raise NotFound("finding not found")
        f.resolved = resolved
        s.commit()


# ---------------------------------------------------------------- rendering & approval


def render_meeting(ws: Workspace, meeting_id: str, *, include_unverified: bool = True) -> str:
    with session_for(ws) as s:
        m = get_meeting(s, meeting_id)
        return _render(s, ws, m, include_unverified=include_unverified)


def _render(s: Session, ws: Workspace, m: Meeting, *, include_unverified: bool) -> str:
    rev = current_agenda(s, m.id, approved_only=True) or current_agenda(s, m.id)
    tree = from_dicts(rev.tree) if rev else []
    notes = [
        n
        for n in _notes_as_dicts(list(s.scalars(select(Note).where(Note.meeting_id == m.id))))
        if n["status"] != "unsupported" and (include_unverified or n["status"] != "manual_unverified")
    ]
    extra = []
    if m.carry_forward:
        # Only items that still qualify (open, on this agenda, undiscussed) are carried forward.
        still = {c["follow_up_id"]: c["description"] for c in _undiscussed(s, m)}
        extra = list(dict.fromkeys(still[x] for x in m.carry_forward if x in still))
    complete = bool((m.coverage or {}).get("complete"))
    return render.render(
        title=f"{ws.name} Meeting Notes",
        meeting_date=m.meeting_date,
        tree=tree,
        notes=notes,
        coverage_complete=complete,
        extra_follow_ups=extra,
    )


def approve_notes(ws: Workspace, meeting_id: str) -> NotesRevision:
    """Approve the current notes: immutable snapshot + propose historical updates."""
    from .models import now

    with session_for(ws) as s:
        m = get_meeting(s, meeting_id)
        if m.state not in ("ready_for_approval", "review_required"):
            raise ServiceError(f"Meeting in state {m.state} cannot be approved")
        if not m.meeting_date:
            raise ServiceError("Confirm the meeting date before approving.")
        open_high = s.scalars(
            select(ReviewFinding).where(
                ReviewFinding.meeting_id == meeting_id,
                ReviewFinding.severity == "high",
                ReviewFinding.resolved.is_(False),
            )
        ).all()
        live_note_ids = {
            n.id for n in s.scalars(select(Note).where(Note.meeting_id == meeting_id, Note.deleted.is_(False)))
        }
        # In review_required every open high finding blocks; once converged, the fresh post-edit checks (round 99) do.
        blocking = [
            f
            for f in open_high
            if (f.note_id is None or f.note_id in live_note_ids) and (m.state == "review_required" or f.round_no == 99)
        ]
        if blocking:
            raise ServiceError(f"{len(blocking)} unresolved high-severity finding(s). Resolve or dismiss them first.")
        md = _render(s, ws, m, include_unverified=True)
        prev = s.scalars(
            select(NotesRevision)
            .where(NotesRevision.meeting_id == meeting_id)
            .order_by(NotesRevision.revision_no.desc())
        ).first()
        rev = NotesRevision(
            meeting_id=meeting_id,
            revision_no=(prev.revision_no + 1 if prev else 1),
            markdown=md,
            notes_snapshot=_notes_as_dicts(list(s.scalars(select(Note).where(Note.meeting_id == meeting_id)))),
            approved_at=now(),
        )
        s.add(rev)
        transition(m, "approved")
        audit(
            s,
            "meeting",
            meeting_id,
            "approved",
            new={"revision_no": rev.revision_no},
            source="user",
            meeting_id=meeting_id,
        )
        s.flush()
        _propose_history(s, m, rev)
        s.commit()
        from .search import index_meeting

        index_meeting(ws, meeting_id)
        return rev


def reopen_meeting(ws: Workspace, meeting_id: str) -> Meeting:
    with session_for(ws) as s:
        m = get_meeting(s, meeting_id)
        transition(m, "ready_for_approval")
        audit(s, "meeting", meeting_id, "reopened", source="user", meeting_id=meeting_id)
        s.commit()
        return m


def _propose_history(s: Session, m: Meeting, rev: NotesRevision) -> None:
    """Historical reconciliation. Everything created here is marked by origin and backed by evidence.

    Transcript-backed follow-ups start as 'proposed' and decisions keep their proposed/confirmed category;
    the user confirms or edits them in the inbox. Existing records are never rewritten; the one exception is a
    still-'proposed' follow-up whose note is gone from this revision, which becomes 'unverified' with an event.
    """
    agenda = current_agenda(s, m.id, approved_only=True)
    texts = {n.id: n.text for n, _ in walk(from_dicts(agenda.tree))} if agenda else {}
    # Claim texts of every note any approved revision held: a row proposed from an earlier note (since regenerated
    # under a new id) is recognised by its claim, not only by note id.
    prior: dict[str, list[str]] = {}
    for r in s.scalars(select(NotesRevision).where(NotesRevision.meeting_id == m.id)):
        for x in r.notes_snapshot:
            prior.setdefault(x["id"], []).extend(t for t in (x.get("text"), x.get("source_text")) if t)
    tables: tuple[Any, ...] = (FollowUp, Decision, CallingEvent, ScheduledEvent)
    rows: dict[Any, list[Any]] = {
        t: list(s.scalars(select(t).where(t.meeting_id == m.id, t.source_note_id.is_not(None)))) for t in tables
    }

    def match(table: Any, n: dict[str, Any]) -> Any:
        for row in rows[table]:
            if row.source_note_id == n["id"] or any(
                evidence.same_claim(n["text"], t) for t in prior.get(row.source_note_id, [])
            ):
                return row
        return None

    def add(table: Any, row: Any) -> None:
        s.add(row)
        s.flush()
        rows[table].append(row)

    backed: set[str] = set()
    for n in rev.notes_snapshot:
        if n["deleted"] or n["status"] == "unsupported" or n["agenda_node_id"] not in texts:
            continue  # only notes the approved Markdown actually renders
        topic = texts[n["agenda_node_id"]]
        # Moving or retagging keeps the transcript's claim; only a content edit makes it the user's.
        origin = "transcript" if n["evidence"] and not n.get("user_content") else "user"
        if n["follow_up"]:
            fu = match(FollowUp, n)
            if fu is None:
                fu = FollowUp(
                    source_note_id=n["id"],
                    meeting_id=m.id,
                    agenda_topic=topic,
                    description=n["text"],
                    owner=n["owner"],
                    status="proposed",
                    origin=origin,
                    evidence=n["evidence"],
                )
                add(FollowUp, fu)
                s.add(
                    FollowUpEvent(
                        follow_up_id=fu.id,
                        meeting_id=m.id,
                        prev_status=None,
                        new_status="proposed",
                        source=origin,
                        evidence=n["evidence"],
                        note="Created from approved meeting notes",
                    )
                )
                audit(
                    s,
                    "follow_up",
                    fu.id,
                    "created",
                    new={"description": fu.description, "status": "proposed"},
                    source=origin,
                    meeting_id=m.id,
                )
            backed.add(fu.id)
        if n["category"] in ("decision", "proposed_decision") and match(Decision, n) is None:
            trusted = origin == "transcript" and n["status"] in ("verified", "user_edited") and not n["stale"]
            status = (
                "confirmed" if n["category"] == "decision" and n["uncertainty"] == "low" and trusted else "proposed"
            )
            if origin == "user":
                status = "unverified"
            d = Decision(
                source_note_id=n["id"],
                meeting_id=m.id,
                agenda_topic=topic,
                text=n["text"],
                status=status,
                origin=origin,
                evidence=n["evidence"],
            )
            add(Decision, d)
            audit(
                s,
                "decision",
                d.id,
                "created",
                new={"text": d.text, "status": status},
                source=origin,
                meeting_id=m.id,
            )
        if n["category"] == "calling_event" and n["calling_event"] and match(CallingEvent, n) is None:
            ce = CallingEvent(
                source_note_id=n["id"],
                meeting_id=m.id,
                person=None,
                calling=topic,
                event_type=n["calling_event"],
                note=n["text"],
                origin=origin,
                evidence=n["evidence"],
            )
            add(CallingEvent, ce)
            audit(
                s,
                "calling_event",
                ce.id,
                "created",
                new={"event_type": ce.event_type, "note": ce.note},
                source=origin,
                meeting_id=m.id,
            )
        if n["category"] == "date" and match(ScheduledEvent, n) is None:
            se = ScheduledEvent(
                source_note_id=n["id"],
                meeting_id=m.id,
                title=n["text"],
                event_date=None,
                origin=origin,
                evidence=n["evidence"],
            )
            add(ScheduledEvent, se)
            audit(
                s,
                "scheduled_event",
                se.id,
                "created",
                new={"title": se.title},
                source=origin,
                meeting_id=m.id,
            )
    for fu in rows[FollowUp]:
        if fu.id not in backed and fu.status == "proposed":
            fu.status = "unverified"
            note = f"No longer a follow-up in approved notes revision {rev.revision_no}"
            s.add(
                FollowUpEvent(
                    follow_up_id=fu.id,
                    meeting_id=m.id,
                    prev_status="proposed",
                    new_status="unverified",
                    source="system",
                    note=note,
                )
            )
            audit(
                s,
                "follow_up",
                fu.id,
                "not_in_approved_notes",
                prev={"status": "proposed"},
                new={"status": "unverified", "revision_no": rev.revision_no},
                source="system",
                meeting_id=m.id,
            )


# ---------------------------------------------------------------- agenda-vs-history (Case B)


def undiscussed_agenda_follow_ups(ws: Workspace, meeting_id: str) -> list[dict[str, Any]]:
    """Case B: open historical follow-ups that appear on this agenda but were not discussed.

    Matching is lexical and advisory; the user decides whether to carry an item forward.
    """
    with session_for(ws) as s:
        return _undiscussed(s, get_meeting(s, meeting_id))


def _undiscussed(s: Session, m: Meeting) -> list[dict[str, Any]]:
    rev = current_agenda(s, m.id, approved_only=True)
    if rev is None:
        return []
    discussed = {
        n.agenda_node_id
        for n in s.scalars(
            select(Note).where(Note.meeting_id == m.id, Note.deleted.is_(False), Note.status != "unsupported")
        )
    }
    open_items = s.scalars(
        select(FollowUp).where(
            FollowUp.status.in_(["open", "proposed", "blocked", "deferred", "unverified"]),
            or_(FollowUp.meeting_id.is_(None), FollowUp.meeting_id != m.id),
        )
    ).all()
    out = []
    for node, _ in walk(from_dicts(rev.tree)):
        if node.id in discussed or node.children:
            continue
        for fu in open_items:
            if (
                evidence.same_claim(node.text, fu.description)
                or node.text.lower() in fu.description.lower()
                or (fu.agenda_topic and fu.agenda_topic.lower() == node.text.lower())
            ):
                out.append(
                    {
                        "agenda_node_id": node.id,
                        "agenda_text": node.text,
                        "follow_up_id": fu.id,
                        "description": fu.description,
                        "status": fu.status,
                        "selected": fu.id in (m.carry_forward or []),
                    }
                )
    return out
