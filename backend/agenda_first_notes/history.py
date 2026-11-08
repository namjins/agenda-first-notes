"""Explicit, audited user updates to historical records. Nothing here is automatic."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from .models import (
    FOLLOW_UP_STATUSES,
    AuditEvent,
    CallingEvent,
    Decision,
    FollowUp,
    FollowUpEvent,
    ScheduledEvent,
)
from .search import index_meeting
from .services import NotFound, ServiceError, audit
from .workspaces import Workspace, session_for


def list_follow_ups(ws: Workspace, status: str | None = None) -> list[FollowUp]:
    with session_for(ws) as s:
        q = select(FollowUp).order_by(FollowUp.updated_at.desc())
        if status:
            q = q.where(FollowUp.status == status)
        items = list(s.scalars(q))
        for i in items:
            _ = i.events  # load
        return items


def update_follow_up(ws: Workspace, follow_up_id: str, data: dict[str, Any]) -> FollowUp:
    with session_for(ws) as s:
        fu = s.get(FollowUp, follow_up_id)
        if fu is None:
            raise NotFound("follow-up not found")
        prev = {
            "status": fu.status,
            "description": fu.description,
            "owner": fu.owner,
            "due_date": fu.due_date,
        }
        status_changed = "status" in data and data["status"] != fu.status
        if "status" in data:
            if data["status"] not in FOLLOW_UP_STATUSES:
                raise ServiceError("invalid status")
            if status_changed:
                s.add(
                    FollowUpEvent(
                        follow_up_id=fu.id,
                        prev_status=fu.status,
                        new_status=data["status"],
                        note=data.get("note"),
                        source="user",
                    )
                )
                fu.status = data["status"]
        if "description" in data and (data["description"] or "").strip():
            fu.description = data["description"].strip()[:2000]
        if "owner" in data:
            fu.owner = (data["owner"] or "").strip()[:120] or None
        if "due_date" in data:
            fu.due_date = data["due_date"] or None
        if (fu.description, fu.owner) != (prev["description"], prev["owner"]):
            fu.origin = "user"  # the item now states the user's wording/owner, not the transcript's
        if data.get("note") and not status_changed:
            s.add(
                FollowUpEvent(
                    follow_up_id=fu.id,
                    prev_status=fu.status,
                    new_status=fu.status,
                    note=data["note"],
                    source="user",
                )
            )
        audit(
            s,
            "follow_up",
            fu.id,
            "user_update",
            prev=prev,
            new={
                "status": fu.status,
                "description": fu.description,
                "owner": fu.owner,
                "due_date": fu.due_date,
                "note": data.get("note"),
            },
            source="user",
            meeting_id=fu.meeting_id,
        )
        s.commit()
        mid = fu.meeting_id
        _ = fu.events
    if mid:
        index_meeting(ws, mid)
    return fu


def create_follow_up(ws: Workspace, data: dict[str, Any]) -> FollowUp:
    with session_for(ws) as s:
        fu = FollowUp(
            meeting_id=data.get("meeting_id"),
            agenda_topic=data.get("agenda_topic"),
            description=data["description"].strip()[:2000],
            owner=(data.get("owner") or None),
            due_date=data.get("due_date") or None,
            status="open",
            origin="user",
            evidence=[],
        )
        s.add(fu)
        s.flush()
        s.add(
            FollowUpEvent(
                follow_up_id=fu.id,
                prev_status=None,
                new_status="open",
                source="user",
                note="Created manually",
            )
        )
        audit(
            s,
            "follow_up",
            fu.id,
            "created",
            new={"description": fu.description, "status": "open"},
            source="user",
            meeting_id=fu.meeting_id,
        )
        s.commit()
        _ = fu.events
        return fu


def list_decisions(ws: Workspace) -> list[Decision]:
    with session_for(ws) as s:
        return list(s.scalars(select(Decision).order_by(Decision.created_at.desc())))


def update_decision(ws: Workspace, decision_id: str, status: str) -> Decision:
    if status not in ("proposed", "confirmed", "unverified"):
        raise ServiceError("invalid status")
    with session_for(ws) as s:
        d = s.get(Decision, decision_id)
        if d is None:
            raise NotFound("decision not found")
        prev = {"status": d.status}
        d.status = status
        audit(
            s,
            "decision",
            d.id,
            "user_update",
            prev=prev,
            new={"status": status},
            source="user",
            meeting_id=d.meeting_id,
        )
        s.commit()
        return d


def list_calling_events(ws: Workspace) -> list[CallingEvent]:
    with session_for(ws) as s:
        return list(s.scalars(select(CallingEvent).order_by(CallingEvent.created_at.desc())))


def add_calling_event(ws: Workspace, data: dict[str, Any]) -> CallingEvent:
    """Explicit administrative entry. Never derived from another event or from elapsed time."""
    from .models import CALLING_EVENTS

    if data.get("event_type") not in CALLING_EVENTS:
        raise ServiceError("invalid calling event type")
    with session_for(ws) as s:
        ce = CallingEvent(
            meeting_id=data.get("meeting_id"),
            person=(data.get("person") or None),
            calling=(data.get("calling") or None),
            event_type=data["event_type"],
            note=data.get("note"),
            origin="user",
            evidence=[],
        )
        s.add(ce)
        s.flush()
        audit(
            s,
            "calling_event",
            ce.id,
            "created",
            new={"event_type": ce.event_type, "person": ce.person, "calling": ce.calling},
            source="user",
            meeting_id=ce.meeting_id,
        )
        s.commit()
        return ce


def update_calling_event(ws: Workspace, event_id: str, data: dict[str, Any]) -> CallingEvent:
    with session_for(ws) as s:
        ce = s.get(CallingEvent, event_id)
        if ce is None:
            raise NotFound("calling event not found")
        prev = {"person": ce.person, "calling": ce.calling, "event_type": ce.event_type}
        if "event_type" in data and data["event_type"] != ce.event_type:
            from .models import CALLING_EVENTS

            if data["event_type"] not in CALLING_EVENTS:
                raise ServiceError("invalid calling event type")
            if ce.origin != "user":
                # Calling events are independent; a different stage is a new event, never an edit of this one.
                raise ServiceError("Record a new calling event instead of changing a transcript-backed one.")
            ce.event_type = data["event_type"]
        for k in ("person", "calling", "note"):
            if k in data:
                setattr(ce, k, (data[k] or "").strip() or None)
        if (ce.person, ce.calling) != (prev["person"], prev["calling"]):
            ce.origin = "user"  # the row now states the user's person/calling, not the transcript's
        audit(
            s,
            "calling_event",
            ce.id,
            "user_update",
            prev=prev,
            new={"person": ce.person, "calling": ce.calling, "event_type": ce.event_type},
            source="user",
            meeting_id=ce.meeting_id,
        )
        s.commit()
        return ce


def list_scheduled_events(ws: Workspace) -> list[ScheduledEvent]:
    with session_for(ws) as s:
        return list(s.scalars(select(ScheduledEvent).order_by(ScheduledEvent.created_at.desc())))


def set_event_date(ws: Workspace, event_id: str, event_date: str, note: str | None = None) -> ScheduledEvent:
    """A changed date never overwrites: a new record supersedes the latest one in the chain."""
    if not (event_date or "").strip():
        raise ServiceError("event_date is required")
    with session_for(ws) as s:
        old = s.get(ScheduledEvent, event_id)
        if old is None:
            raise NotFound("event not found")
        # Changing an already-superseded record changes the current one; never fork the chain.
        while (
            nxt := s.scalars(select(ScheduledEvent).where(ScheduledEvent.supersedes_id == old.id)).first()
        ) is not None:
            old = nxt
        if old.event_date is None:
            old.event_date = event_date.strip()[:40]
            audit(
                s,
                "scheduled_event",
                old.id,
                "date_set",
                new={"event_date": old.event_date, "note": note},
                source="user",
                meeting_id=old.meeting_id,
            )
            s.commit()
            return old
        new = ScheduledEvent(
            meeting_id=old.meeting_id,
            title=old.title,
            event_date=event_date.strip()[:40],
            supersedes_id=old.id,
            origin="user",
            evidence=[],
        )
        s.add(new)
        s.flush()
        audit(
            s,
            "scheduled_event",
            new.id,
            "date_changed",
            prev={"event_date": old.event_date, "id": old.id},
            new={"event_date": new.event_date, "note": note},
            source="user",
            meeting_id=old.meeting_id,
        )
        s.commit()
        return new


def list_audit(ws: Workspace, limit: int = 200, meeting_id: str | None = None) -> list[AuditEvent]:
    with session_for(ws) as s:
        q = select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(max(1, min(limit, 1000)))
        if meeting_id:
            q = q.where(AuditEvent.meeting_id == meeting_id)
        return list(s.scalars(q))
