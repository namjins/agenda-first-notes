"""Workspace registry. Every workspace has its own directory, SQLite database and files. The one exception is the
member list, shared by both workspaces in <data>/members.sqlite3 (maintainer decision 2026-11-08).

Workspace ids are a closed set validated here; no client-supplied path ever becomes a boundary.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from . import config
from .models import SharedBase

WorkspaceId = Literal["ward_council", "bishopric"]
WORKSPACES: dict[str, str] = {"ward_council": "Ward Council", "bishopric": "Bishopric"}

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,120}$")


class UnknownWorkspace(ValueError):
    pass


def validate_workspace_id(value: str) -> WorkspaceId:
    if value not in WORKSPACES:
        raise UnknownWorkspace(f"Unknown workspace: {value!r}")
    return value  # type: ignore[return-value]


def safe_filename(name: str) -> str:
    """Flatten any user-supplied filename to a single safe path component."""
    base = os.path.basename(name.replace("\\", "/")).strip() or "file"
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base)[:120].strip(". ") or "file"
    if not _SAFE_NAME.match(base):
        base = "file"
    return base


@dataclass(frozen=True)
class Workspace:
    id: WorkspaceId
    name: str
    root: Path

    @property
    def db_path(self) -> Path:
        return self.root / "notes.sqlite3"

    @property
    def uploads(self) -> Path:
        return self.root / "uploads"

    @property
    def references(self) -> Path:
        return self.root / "references"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    def file_path(self, folder: Path, name: str) -> Path:
        """Resolve a filename inside a workspace folder, refusing traversal and symlink escapes."""
        if folder not in (self.uploads, self.references, self.exports):
            raise ValueError("folder is not a workspace folder")
        folder.mkdir(parents=True, exist_ok=True)
        candidate = (folder / safe_filename(name)).resolve()
        if candidate.parent != folder.resolve() or candidate.is_symlink():
            raise ValueError("path escapes workspace folder")
        return candidate


_engines: dict[tuple[Path, str], Engine] = {}


def get_workspace(workspace_id: str) -> Workspace:
    wid = validate_workspace_id(workspace_id)
    root = config.data_dir() / "workspaces" / wid
    for d in (root, root / "uploads", root / "references", root / "exports"):
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return Workspace(id=wid, name=WORKSPACES[wid], root=root)


def engine_for(ws: Workspace) -> Engine:
    return _engine(ws.db_path, ws.id)


def _engine(path: Path, key_id: str) -> Engine:
    key = (path, key_id)
    eng = _engines.get(key)
    if eng is None:
        # hide_parameters: SQL errors never echo row values (member names, transcript text) into logs or replies
        eng = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False}, hide_parameters=True)

        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _record):  # type: ignore[no-untyped-def]
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA secure_delete=ON")  # deleted meeting content is overwritten, not left in free pages
            cur.close()

        _engines[key] = eng
    return eng


def session_for(ws: Workspace) -> Session:
    return sessionmaker(bind=engine_for(ws), expire_on_commit=False)()


def members_db_path() -> Path:
    """The member list both workspaces share (maintainer decision 2026-11-08): the only data outside a workspace."""
    return config.data_dir() / "members.sqlite3"


def members_engine() -> Engine:
    path = members_db_path()
    fresh = (path, "members") not in _engines
    eng = _engine(path, "members")
    if fresh:
        # shortcut: one table, created if missing (no Alembic); add a migration path if its schema ever changes
        SharedBase.metadata.create_all(eng)
    return eng


def members_session() -> Session:
    return sessionmaker(bind=members_engine(), expire_on_commit=False)()


def checkpoint(db: Workspace | Engine, tries: int = 3) -> bool:
    """Move committed pages out of the WAL file and truncate it, so deleted or replaced data does not linger there
    (secure_delete zeroes freed pages in the database file only). False when readers kept it busy throughout; each
    try waits at most 0.5 s for them, so the caller's warning comes within about 2 s. `db`: a workspace or an engine
    (members_engine() for the shared member list)."""
    with (db if isinstance(db, Engine) else engine_for(db)).connect() as conn:
        prev = int(conn.exec_driver_sql("PRAGMA busy_timeout").scalar() or 0)
        conn.exec_driver_sql("PRAGMA busy_timeout=500")
        try:
            for i in range(tries):
                busy = conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if busy is None or not busy[0]:
                    return True
                time.sleep(0.05 * (i + 1))
            return False
        finally:
            conn.exec_driver_sql(f"PRAGMA busy_timeout={prev}")  # the pooled connection keeps its usual timeout


def reset_engines() -> None:
    """Tests switch AFN_DATA_DIR between runs; drop cached engines."""
    for eng in _engines.values():
        eng.dispose()
    _engines.clear()
