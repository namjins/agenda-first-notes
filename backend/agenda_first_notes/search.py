"""SQLite FTS5 search, strictly per workspace (each workspace has its own database)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, text

from .models import CallingEvent, Decision, FollowUp, Meeting, NotesRevision
from .workspaces import Workspace, engine_for, session_for


def index_meeting(ws: Workspace, meeting_id: str) -> None:
    with session_for(ws) as s:
        m = s.get(Meeting, meeting_id)
        if m is None:
            return
        rows: list[dict[str, Any]] = []
        rev = s.scalars(
            select(NotesRevision)
            .where(NotesRevision.meeting_id == meeting_id)
            .order_by(NotesRevision.revision_no.desc())
        ).first()
        if rev:
            rows.append(
                {
                    "entity_type": "meeting",
                    "entity_id": meeting_id,
                    "meeting_id": meeting_id,
                    "meeting_date": m.meeting_date or "",
                    "status": m.state,
                    "title": m.title or f"Meeting {m.meeting_date or ''}",
                    "body": rev.markdown,
                }
            )
        for d in s.scalars(select(Decision).where(Decision.meeting_id == meeting_id)):
            rows.append(
                {
                    "entity_type": "decision",
                    "entity_id": d.id,
                    "meeting_id": meeting_id,
                    "meeting_date": m.meeting_date or "",
                    "status": d.status,
                    "title": d.agenda_topic or "Decision",
                    "body": d.text,
                }
            )
        for f in s.scalars(select(FollowUp).where(FollowUp.meeting_id == meeting_id)):
            rows.append(
                {
                    "entity_type": "follow_up",
                    "entity_id": f.id,
                    "meeting_id": meeting_id,
                    "meeting_date": m.meeting_date or "",
                    "status": f.status,
                    "title": f.agenda_topic or "Follow-up",
                    "body": f.description,
                }
            )
        for c in s.scalars(select(CallingEvent).where(CallingEvent.meeting_id == meeting_id)):
            rows.append(
                {
                    "entity_type": "calling_event",
                    "entity_id": c.id,
                    "meeting_id": meeting_id,
                    "meeting_date": m.meeting_date or "",
                    "status": c.event_type,
                    "title": c.calling or "Calling",
                    "body": c.note or "",
                }
            )
    with engine_for(ws).begin() as conn:
        conn.execute(text("DELETE FROM search_index WHERE meeting_id = :m"), {"m": meeting_id})
        for r in rows:
            conn.execute(
                text(
                    "INSERT INTO search_index(entity_type, entity_id, meeting_id, meeting_date, status, title, body) VALUES (:entity_type, :entity_id, :meeting_id, :meeting_date, :status, :title, :body)"
                ),
                r,
            )


def reindex_all(ws: Workspace) -> int:
    with session_for(ws) as s:
        ids = [m.id for m in s.scalars(select(Meeting))]
    for mid in ids:
        index_meeting(ws, mid)
    return len(ids)


def _fts_query(q: str) -> str:
    # Quote each term so user punctuation cannot break FTS syntax; prefix match on the last term.
    terms = [t.replace('"', '""') for t in q.split() if t.strip()]
    if not terms:
        return ""
    return " ".join(f'"{t}"' for t in terms[:-1]) + (" " if len(terms) > 1 else "") + f'"{terms[-1]}"*'


def search(
    ws: Workspace,
    q: str,
    *,
    entity_type: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    fq = _fts_query(q)
    if not fq:
        return []
    sql = "SELECT entity_type, entity_id, meeting_id, meeting_date, status, title, snippet(search_index, 6, '[', ']', '…', 24) AS snippet, bm25(search_index) AS rank FROM search_index WHERE search_index MATCH :q"
    params: dict[str, Any] = {"q": fq, "limit": max(1, min(limit, 200))}
    if entity_type:
        sql += " AND entity_type = :et"
        params["et"] = entity_type
    if status:
        sql += " AND status = :st"
        params["st"] = status
    if date_from:
        sql += " AND meeting_date >= :df"
        params["df"] = date_from
    if date_to:
        sql += " AND meeting_date <= :dt"
        params["dt"] = date_to
    sql += " ORDER BY rank LIMIT :limit"
    with engine_for(ws).connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(sql), params)]
