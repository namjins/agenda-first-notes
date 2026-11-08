"""SQLAlchemy ORM. One schema, instantiated once per workspace database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON, list: JSON}


class Stamped:
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(default=now)


MEETING_STATES = [
    "draft",
    "agenda_imported",
    "agenda_verified",
    "transcript_added",
    "processing",
    "review_required",
    "ready_for_approval",
    "approved",
    "archived",
    "failed",
]

# Valid transitions. Anything not listed is rejected by services.transition().
TRANSITIONS: dict[str, set[str]] = {
    "draft": {"agenda_imported", "agenda_verified", "transcript_added", "archived"},
    "agenda_imported": {
        "agenda_imported",
        "agenda_verified",
        "transcript_added",
        "draft",
        "archived",
    },
    "agenda_verified": {
        "agenda_imported",
        "agenda_verified",
        "transcript_added",
        "processing",
        "archived",
    },
    "transcript_added": {
        "agenda_imported",
        "agenda_verified",
        "transcript_added",
        "processing",
        "archived",
    },
    "processing": {"review_required", "ready_for_approval", "failed", "transcript_added"},
    "review_required": {
        "processing",
        "ready_for_approval",
        "approved",
        "transcript_added",
        "agenda_verified",
        "archived",
    },
    "ready_for_approval": {
        "processing",
        "review_required",
        "approved",
        "transcript_added",
        "agenda_verified",
        "archived",
    },
    "approved": {"ready_for_approval", "archived"},
    "archived": {"approved", "draft"},
    "failed": {"processing", "transcript_added", "agenda_verified", "archived"},
}


class Meeting(Stamped, Base):
    __tablename__ = "meetings"
    title: Mapped[str] = mapped_column(String(200), default="")
    meeting_date: Mapped[str | None] = mapped_column(String(10))  # ISO date, confirmed by user
    state: Mapped[str] = mapped_column(String(32), default="draft")
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)
    coverage: Mapped[dict | None] = mapped_column(JSON)  # {"segments": [...], "complete": bool}
    carry_forward: Mapped[list] = mapped_column(JSON, default=list)  # follow_up ids selected for next agenda
    last_error: Mapped[str | None] = mapped_column(Text)

    agenda_revisions: Mapped[list[AgendaRevision]] = relationship(
        back_populates="meeting", cascade="all, delete-orphan"
    )
    transcripts: Mapped[list[TranscriptVersion]] = relationship(back_populates="meeting", cascade="all, delete-orphan")
    notes: Mapped[list[Note]] = relationship(back_populates="meeting", cascade="all, delete-orphan")


class AgendaRevision(Stamped, Base):
    __tablename__ = "agenda_revisions"
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    revision_no: Mapped[int] = mapped_column(Integer)
    tree: Mapped[list] = mapped_column(JSON)  # list[AgendaNode] as dicts
    source: Mapped[str] = mapped_column(String(32))  # manual | text | image | proposed | edit
    approved_at: Mapped[datetime | None]
    meeting: Mapped[Meeting] = relationship(back_populates="agenda_revisions")


class AgendaImage(Stamped, Base):
    __tablename__ = "agenda_images"
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64))
    extracted_text: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0)


class TranscriptVersion(Stamped, Base):
    __tablename__ = "transcript_versions"
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(32))  # paste | upload | correction
    reason: Mapped[str | None] = mapped_column(Text)  # why a correction was made
    parent_id: Mapped[str | None] = mapped_column(String(32))
    meeting: Mapped[Meeting] = relationship(back_populates="transcripts")


NOTE_STATUSES = [
    "verified",
    "uncertain",
    "user_edited",
    "needs_review",
    "unsupported",
    "manual_unverified",
]
NOTE_CATEGORIES = [
    "decision",
    "proposed_decision",
    "assignment",
    "calling_event",
    "date",
    "status_change",
    "clarification",
    "open_question",
    "dependency",
    "follow_up_candidate",
    "completion",
    "uncertainty",
    "discussion",
]


class Note(Stamped, Base):
    __tablename__ = "notes"
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    agenda_node_id: Mapped[str | None] = mapped_column(String(32))  # None = unmapped finding
    position: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(32), default="discussion")
    uncertainty: Mapped[str] = mapped_column(String(16), default="low")  # low | medium | high
    follow_up: Mapped[bool] = mapped_column(Boolean, default=False)
    owner: Mapped[str | None] = mapped_column(String(120))
    calling_event: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="needs_review")
    origin: Mapped[str] = mapped_column(String(16), default="model")  # model | user
    evidence: Mapped[list] = mapped_column(JSON, default=list)  # [{"start","end","quote","transcript_version_id"}]
    stale: Mapped[bool] = mapped_column(Boolean, default=False)  # source changed since verification
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)
    meeting: Mapped[Meeting] = relationship(back_populates="notes")


class NotesRevision(Stamped, Base):
    __tablename__ = "notes_revisions"
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    revision_no: Mapped[int] = mapped_column(Integer)
    markdown: Mapped[str] = mapped_column(Text)
    notes_snapshot: Mapped[list] = mapped_column(JSON)
    approved_at: Mapped[datetime | None]


class ReviewFinding(Stamped, Base):
    __tablename__ = "review_findings"
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    round_no: Mapped[int] = mapped_column(Integer)
    note_id: Mapped[str | None] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))  # info | low | high
    category: Mapped[str] = mapped_column(String(48))
    message: Mapped[str] = mapped_column(Text)
    action: Mapped[str | None] = mapped_column(String(32))  # applied automatic action, if any
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(16), default="deterministic")  # deterministic | model


class Job(Stamped, Base):
    __tablename__ = "jobs"
    meeting_id: Mapped[str | None] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|failed|cancelled
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    checkpoint: Mapped[dict | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)


FOLLOW_UP_STATUSES = ["proposed", "open", "blocked", "deferred", "completed", "unverified"]


class FollowUp(Stamped, Base):
    __tablename__ = "follow_ups"
    meeting_id: Mapped[str | None] = mapped_column(String(32), index=True)  # source meeting
    agenda_topic: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(String(120))
    due_date: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(16), default="proposed")
    origin: Mapped[str] = mapped_column(String(16), default="transcript")  # transcript | user
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)
    events: Mapped[list[FollowUpEvent]] = relationship(
        cascade="all, delete-orphan", order_by="FollowUpEvent.created_at"
    )


class FollowUpEvent(Stamped, Base):
    __tablename__ = "follow_up_events"
    follow_up_id: Mapped[str] = mapped_column(ForeignKey("follow_ups.id", ondelete="CASCADE"), index=True)
    meeting_id: Mapped[str | None] = mapped_column(String(32))
    prev_status: Mapped[str | None] = mapped_column(String(16))
    new_status: Mapped[str | None] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16))  # transcript | user
    evidence: Mapped[list] = mapped_column(JSON, default=list)


class Decision(Stamped, Base):
    __tablename__ = "decisions"
    meeting_id: Mapped[str] = mapped_column(String(32), index=True)
    agenda_topic: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))  # proposed | confirmed | unverified
    origin: Mapped[str] = mapped_column(String(16), default="transcript")
    evidence: Mapped[list] = mapped_column(JSON, default=list)


CALLING_EVENTS = [
    "suggested",
    "under_consideration",
    "approved_to_proceed",
    "extended",
    "accepted",
    "declined",
    "sustained",
    "set_apart",
    "released",
    "not_confirmed",
]


class CallingEvent(Stamped, Base):
    __tablename__ = "calling_events"
    meeting_id: Mapped[str | None] = mapped_column(String(32), index=True)
    person: Mapped[str | None] = mapped_column(String(120))  # may be None/unclear
    calling: Mapped[str | None] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(16), default="transcript")
    evidence: Mapped[list] = mapped_column(JSON, default=list)


class ScheduledEvent(Stamped, Base):
    __tablename__ = "scheduled_events"
    meeting_id: Mapped[str | None] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(Text)
    event_date: Mapped[str | None] = mapped_column(String(40))  # as stated; may be non-ISO text
    supersedes_id: Mapped[str | None] = mapped_column(String(32))
    origin: Mapped[str] = mapped_column(String(16), default="transcript")
    evidence: Mapped[list] = mapped_column(JSON, default=list)


class AuditEvent(Stamped, Base):
    """Append-only. Every consequential historical change writes one row."""

    __tablename__ = "audit_events"
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_id: Mapped[str] = mapped_column(String(32), index=True)
    meeting_id: Mapped[str | None] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(32))
    prev: Mapped[dict | None] = mapped_column(JSON)
    new: Mapped[dict | None] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(16))  # transcript | user | system


class ReferenceDocument(Stamped, Base):
    __tablename__ = "reference_documents"
    filename: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))  # txt | md | pdf | docx
    sha256: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    use_as_glossary: Mapped[bool] = mapped_column(Boolean, default=False)
