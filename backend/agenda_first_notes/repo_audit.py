"""Repository content audit: refuse to commit anything that looks like real data or secrets.

Runs cross-platform from pre-commit (`agenda-first-notes audit --staged`) and from CI (whole tree).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from .config import REPO_ROOT

SUSPICIOUS_SUFFIXES = (
    ".sqlite", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".db", ".db-wal", ".db-shm", ".bak",
    ".pem", ".key", ".p12", ".pfx", ".env", ".heic", ".docx", ".pdf", ".zip", ".log",
)  # fmt: skip
SUSPICIOUS_BARE_NAMES = {"api_token", "settings.json", ".env"}
SUSPICIOUS_NAMES = re.compile(r"(transcript|agenda|minutes|roster|membership|members|export|backup)", re.I)
FIXTURE_DIRS = ("backend/tests/fixtures/", "frontend/e2e/fixtures/")  # may hold data-like files, synthetic only
NAME_EXEMPT_DIRS = (*FIXTURE_DIRS, "docs/")  # documentation may mention agendas/transcripts in file names
MARKER_SUFFIXES = {".txt", ".json", ".csv"}
SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
]
MAX_BYTES = 2 * 1024 * 1024
REQUIRED_FIXTURE_MARKER = "SYNTHETIC"
BINARY_OK = {".png", ".jpg", ".jpeg", ".webp", ".pyc", ".woff", ".woff2", ".ico"}


def _files(staged_only: bool) -> list[Path]:
    cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"] if staged_only else ["git", "ls-files"]
    out = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=False)  # noqa: S603
    return [REPO_ROOT / line for line in out.stdout.splitlines() if line.strip()]


def audit_paths(paths: list[Path]) -> list[str]:
    problems: list[str] = []
    for path in paths:
        if not path.exists() or path.is_dir():
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        name = path.name.lower()
        suffix = path.suffix.lower()
        if name in SUSPICIOUS_BARE_NAMES and not rel.startswith("frontend/"):
            problems.append(f"{rel}: runtime file must not be committed")
        if name.endswith(SUSPICIOUS_SUFFIXES) and not rel.startswith(FIXTURE_DIRS):
            problems.append(f"{rel}: file type {path.suffix} is not allowed in the repository")
        if (
            SUSPICIOUS_NAMES.search(path.name)
            and not rel.startswith(NAME_EXEMPT_DIRS)
            and suffix in {".txt", ".md", ".json", ".csv", *BINARY_OK}
        ):
            problems.append(
                f"{rel}: name suggests meeting/member data; move it out of the repo or into a synthetic fixtures folder"
            )
        if path.stat().st_size > MAX_BYTES:
            problems.append(f"{rel}: larger than 2 MB")
        if suffix in BINARY_OK:
            continue
        try:
            text = path.read_text("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                problems.append(f"{rel}: looks like it contains a secret ({pat.pattern[:30]}...)")
                break
        if rel.startswith(NAME_EXEMPT_DIRS) and suffix in MARKER_SUFFIXES and REQUIRED_FIXTURE_MARKER not in text:
            problems.append(
                f"{rel}: fixture/data file lacks the '{REQUIRED_FIXTURE_MARKER}' marker proving it is fictional"
            )
    return problems


def run_audit(staged_only: bool = False) -> int:
    problems = audit_paths(_files(staged_only))
    if problems:
        print("Repository audit FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print("Repository audit passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run_audit("--staged" in sys.argv))
