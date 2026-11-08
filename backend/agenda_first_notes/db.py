"""Run Alembic migrations against a workspace database."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config

from alembic import command

from .workspaces import Workspace, engine_for

_ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def migrate(ws: Workspace) -> None:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_ALEMBIC_INI.parent / "alembic"))
    with engine_for(ws).begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
