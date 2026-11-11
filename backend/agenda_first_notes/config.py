"""Runtime paths and global (non-sensitive) settings.

All runtime data lives under the OS application-data directory, never in the repository.
Override with AFN_DATA_DIR (used by tests and by people who want a custom location).
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir
from pydantic import BaseModel, Field

APP_NAME = "AgendaFirstNotes"
REPO_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    override = os.environ.get("AFN_DATA_DIR")
    path = Path(override).expanduser().resolve() if override else Path(user_data_dir(APP_NAME, appauthor=False))
    path.mkdir(parents=True, exist_ok=True)
    return path


def assert_outside_repo(path: Path) -> None:
    """Refuse to run with runtime data inside the source checkout (defense in depth)."""
    try:
        path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return
    raise RuntimeError(
        f"Runtime data directory {path} is inside the repository {REPO_ROOT}. Set AFN_DATA_DIR elsewhere."
    )


class ModelSettings(BaseModel):
    ollama_host: str = "http://127.0.0.1:11434"
    primary_model: str | None = None
    reviewer_model: str | None = None  # None = reuse primary model
    vision_model: str | None = None
    context_length: int = Field(default=32768, ge=2048, le=262144)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_output_tokens: int = Field(
        default=12288, ge=256, le=65536
    )  # dense 24k-char segments overflow 4096 (JSON cut off)
    max_review_rounds: int = Field(default=0, ge=0, le=10)  # off: rounds lost facts in real tests
    segment_chars: int = Field(default=24000, ge=2000, le=400000)
    segment_overlap_chars: int = Field(default=1500, ge=0, le=20000)
    think: bool = False  # reasoning models think before answering (extract/review/agenda); off = faster
    review_by_segment: bool = False  # review long transcripts part by part (sees all of it; more requests)


class GlobalSettings(BaseModel):
    models: ModelSettings = ModelSettings()
    theme: str = "system"


def settings_path() -> Path:
    return data_dir() / "settings.json"


def load_settings() -> GlobalSettings:
    p = settings_path()
    if p.exists():
        return GlobalSettings.model_validate(json.loads(p.read_text("utf-8")))
    return GlobalSettings()


def save_settings(s: GlobalSettings) -> None:
    _write_private(settings_path(), s.model_dump_json(indent=2))


def api_token() -> str:
    """Per-installation random token required on every API call (see security.py)."""
    p = data_dir() / "api_token"
    if p.exists():
        tok = p.read_text("utf-8").strip()
        if tok:
            return tok
    tok = secrets.token_urlsafe(32)
    _write_private(p, tok)
    return tok


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # private from the first byte
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        path.chmod(0o600)
    except OSError:  # Windows/other filesystems without POSIX modes
        pass


def dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)
