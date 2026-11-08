"""Repository content audit: refuse to commit anything that looks like real data or secrets.

Runs cross-platform from pre-commit (`agenda-first-notes audit --staged`) and from CI (whole tree).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from .config import REPO_ROOT

SUSPICIOUS_SUFFIXES = {
    ".sqlite",
    ".sqlite3",
    ".db",
    ".db-wal",
    ".db-shm",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".env",
    ".heic",
    ".docx",
    ".pdf",
    ".zip",
    ".log",
}
SUSPICIOUS_NAMES = re.compile(
    r"(transcript|agenda|minutes|roster|membership|members|export|backup|api_token|settings\.json)",
    re.I,
)
ALLOWED_DIRS = ("backend/tests/fixtures/", "docs/", "frontend/e2e/fixtures/")
SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
]
MAX_BYTES = 2 * 1024 * 1024
REQUIRED_FIXTURE_MARKER = "SYNTHETIC"


def _files(staged_only: bool) -> list[Path]:
    cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"] if staged_only else ["git", "ls-files"]
    out = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=False)  # noqa: S603
    return [REPO_ROOT / line for line in out.stdout.splitlines() if line.strip()]


def audit_paths(paths: list[Path]) -> list[str]:
    problems: list[str] = []
    for path in paths:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if not path.exists() or path.is_dir():
            continue
        if path.suffix.lower() in SUSPICIOUS_SUFFIXES and not rel.startswith(ALLOWED_DIRS):
            problems.append(f"{rel}: file type {path.suffix} is not allowed in the repository")
        if (
            SUSPICIOUS_NAMES.search(path.name)
            and not rel.startswith(ALLOWED_DIRS)
            and path.suffix.lower() in {".txt", ".md", ".json", ".csv", ".png", ".jpg", ".jpeg", ".webp"}
        ):
            problems.append(
                f"{rel}: name suggests meeting/member data; move it out of the repo or into a synthetic fixtures folder"
            )
        if path.stat().st_size > MAX_BYTES:
            problems.append(f"{rel}: larger than 2 MB")
        if path.suffix.lower() in {
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".pyc",
            ".woff",
            ".woff2",
            ".ico",
        }:
            continue
        try:
            text = path.read_text("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                problems.append(f"{rel}: looks like it contains a secret ({pat.pattern[:30]}...)")
                break
        if (
            rel.startswith("backend/tests/fixtures/")
            and path.suffix.lower() in {".txt", ".md"}
            and REQUIRED_FIXTURE_MARKER not in text
        ):
            problems.append(f"{rel}: fixture lacks the '{REQUIRED_FIXTURE_MARKER}' marker proving it is fictional")
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
