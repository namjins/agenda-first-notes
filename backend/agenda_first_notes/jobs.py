"""Durable single-worker job runner. Jobs are rows in the workspace DB; one thread runs them in order.

shortcut: one global worker thread keeps model inference sequential (memory); per-workspace workers
would only matter if two workspaces should process simultaneously.
"""

from __future__ import annotations

import sys
import threading
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import IO

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from .models import Job, Meeting
from .services import ServiceError, transition
from .workspaces import Workspace, get_workspace, session_for

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="afn-job")
_lock = threading.Lock()

Runner = Callable[[Workspace, Job, Callable[[float, str], None], Callable[[], bool]], None]
_runners: dict[str, Runner] = {}
_mine: set[str] = set()  # jobs this process queued; recovery never touches them


def register(kind: str, runner: Runner) -> None:
    _runners[kind] = runner


def enqueue(ws: Workspace, kind: str, meeting_id: str | None) -> Job:
    if ws.root / "worker.lock" not in _owned:
        # Only the copy of the app that owns the workspace's jobs runs them; a second copy refuses instead of queueing.
        if not own_workers(ws):
            raise ServiceError("Another running copy of the app is using this workspace; use that window.")
        recover_interrupted(ws)  # took the lock over from a copy that has exited: fail what it left behind
    with _lock, session_for(ws) as s:
        if meeting_id:
            active = s.scalars(
                select(Job).where(
                    Job.meeting_id == meeting_id,
                    Job.kind == kind,
                    Job.status.in_(["queued", "running"]),
                )
            ).first()
            if active:
                return active  # duplicate prevention: one active job per (meeting, kind)
        job = Job(meeting_id=meeting_id, kind=kind, status="queued", message="Queued")
        s.add(job)
        s.commit()
        _mine.add(job.id)
    _executor.submit(_run, ws.id, job.id)
    return job


def _run(workspace_id: str, job_id: str) -> None:
    ws = get_workspace(workspace_id)

    def report(p: float, msg: str) -> None:
        with session_for(ws) as s:
            j = s.get(Job, job_id)
            if j is not None:
                j.progress = max(0.0, min(1.0, p))
                j.message = msg[:500]
                s.commit()

    def should_cancel() -> bool:
        with session_for(ws) as s:
            j = s.get(Job, job_id)
            return bool(j and j.cancel_requested)

    with session_for(ws) as s:
        j = s.get(Job, job_id)
        if j is None or j.status != "queued":
            return
        if j.cancel_requested:
            j.status = "cancelled"
            j.message = "Cancelled before start"
            s.commit()
            return
        j.status = "running"
        j.message = "Starting"
        kind = j.kind
        s.commit()
    try:
        _runners[kind](ws, _get(ws, job_id), report, should_cancel)
        status, msg = "done", "Finished"
    except Exception as e:  # noqa: BLE001 - job outcome is recorded, never hidden
        from .services import Cancelled

        if isinstance(e, Cancelled):
            status, msg = "cancelled", "Cancelled"
        else:
            status, msg = "failed", f"{type(e).__name__}: {e}"
            traceback.print_exc()

    def finish() -> None:
        with session_for(ws) as s:
            j = s.get(Job, job_id)
            if j is None:
                return
            j.status = status
            if status == "done":
                j.progress = 1.0
            if status != "done" or not j.message:
                j.message = msg[:500]
            m = s.get(Meeting, j.meeting_id) if j.meeting_id else None
            if m is not None and m.state == "processing":  # the runner could not record its own outcome
                transition(m, "transcript_added" if status == "cancelled" else "failed")
                m.last_error = m.last_error or msg[:2000]
            s.commit()

    _retry(finish)


def _retry(fn: Callable[[], None], tries: int = 8) -> None:
    """Outcome writes must land: a transient 'database is locked' must not leave a job 'running' with no worker."""
    for i in range(tries):
        try:
            fn()
            return
        except OperationalError:
            if i == tries - 1:
                raise  # still 'running': the next start's recovery marks it failed
            time.sleep(min(0.2 * 2**i, 5.0))


def _get(ws: Workspace, job_id: str) -> Job:
    with session_for(ws) as s:
        j = s.get(Job, job_id)
        assert j is not None
        return j


def cancel(ws: Workspace, job_id: str) -> Job | None:
    with session_for(ws) as s:
        j = s.get(Job, job_id)
        if j is not None and j.status in ("queued", "running"):
            j.cancel_requested = True
            s.commit()
        return j


_owned: dict[Path, IO[str]] = {}


def own_workers(ws: Workspace) -> bool:
    """Take the workspace's worker lock for the life of this process. False when another live process holds it.

    The lock is released when the process exits; a copy that could not take it refuses new jobs until it can.
    """
    path = ws.root / "worker.lock"
    if path in _owned:
        return True
    f = path.open("a+")
    try:
        if sys.platform == "win32":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return False
    _owned[path] = f
    return True


def recover_interrupted(ws: Workspace) -> int:
    """At startup, jobs left 'running' by a crash are marked failed so the user can retry.

    Only when no other live process (a running server) owns this workspace's jobs: `migrate` or a second `serve`
    must not fail a job that is still being processed.
    """
    if not own_workers(ws):
        return 0
    with session_for(ws) as s:
        n = 0
        for j in s.scalars(select(Job).where(Job.status.in_(["queued", "running"]), Job.id.not_in(_mine))):
            j.status = "failed"
            j.message = "Interrupted by application restart; retry to continue."
            m = s.get(Meeting, j.meeting_id) if j.meeting_id else None
            if m is not None and m.state == "processing":  # else edits and delete stay refused until a re-run
                transition(m, "failed")
                m.last_error = j.message
            n += 1
        s.commit()
        return n
