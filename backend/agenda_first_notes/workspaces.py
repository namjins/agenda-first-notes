"""Workspace registry. Every workspace has its own directory, SQLite database and files.

Workspace ids are a closed set validated here; no client-supplied path ever becomes a boundary.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from . import config

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
        d.mkdir(parents=True, exist_ok=True)
    return Workspace(id=wid, name=WORKSPACES[wid], root=root)


def engine_for(ws: Workspace) -> Engine:
    key = (ws.db_path, ws.id)
    eng = _engines.get(key)
    if eng is None:
        eng = create_engine(f"sqlite:///{ws.db_path}", connect_args={"check_same_thread": False})

        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _record):  # type: ignore[no-untyped-def]
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        _engines[key] = eng
    return eng


def session_for(ws: Workspace) -> Session:
    return sessionmaker(bind=engine_for(ws), expire_on_commit=False)()


def reset_engines() -> None:
    """Tests switch AFN_DATA_DIR between runs; drop cached engines."""
    for eng in _engines.values():
        eng.dispose()
    _engines.clear()
