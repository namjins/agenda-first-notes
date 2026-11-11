"""Repository content audit: refuse to commit anything that looks like real data or secrets.

Runs cross-platform from pre-commit (`agenda-first-notes audit --staged`) and from CI (whole tree).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from .config import REPO_ROOT
from .names import _DATE

SPREADSHEETS = (".xlsx", ".xls", ".tsv", ".ods", ".numbers")  # member lists and rosters
SUSPICIOUS_SUFFIXES = (
    ".sqlite", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".db", ".db-wal", ".db-shm", ".bak",
    ".pem", ".key", ".p12", ".pfx", ".env", ".heic", ".docx", ".pdf", ".zip", ".log",
    *SPREADSHEETS,
)  # fmt: skip
# Runtime files and their copies/backups (api_token copy, api_token-old, settings (1).json, settings.json~ ...);
# not source like api.py, and not committed templates such as .env.example or settings.json.sample.
# A template suffix never rescues an api_token name.
RUNTIME_FILE = re.compile(r"^(api_token|(settings.*\.json|\.env)(?!.*\.(example|sample|template)$))", re.I)
# The app's own export names ({workspace}_{date|undated}_{id8}.md/.txt), flagged anywhere, docs/ included.
EXPORT_NAME = re.compile(r"(ward_council|bishopric)_(\d{4}-\d{2}-\d{2}|undated)_[0-9a-f]{8}", re.I)
# Workspace ids catch the app's own export names, e.g. bishopric_2026-03-01_1a2b3c4d.md.
SUSPICIOUS_NAMES = re.compile(
    r"(transcript|agenda|minutes|roster|membership|members?|directory|ward.?list|export|backup|ward_council|bishopric)",
    re.I,
)
FIXTURE_DIRS = (
    "backend/tests/fixtures/",
    "frontend/e2e/fixtures/",
    "tools/prompt_tuning/data/synthetic/",
)  # may hold data-like files, synthetic only
NAME_EXEMPT_DIRS = (*FIXTURE_DIRS, "docs/")  # documentation may mention agendas/transcripts in file names
MARKER_SUFFIXES = {".txt", ".json", ".csv"}
FIXTURE_MARKER_SUFFIXES = {*MARKER_SUFFIXES, ".md"}  # docs/*.md are prose, fixture .md files are data
SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
]
# The member-list CSV heading (the column "Callings with ..." is distinctive), or member rows without it (three or
# more sustained dates, in any format the parser reads, on CSV/TSV-like lines: a quoted "Last, First" field or a tab):
# real member data in any file. Allowed only in SYNTHETIC-marked tests and fixtures. Dated prose lines never count.
MEMBER_LIST = re.compile(r"callings\s+with\s+date\s+sustained|preferred\s+name\s*,\s*full\s+name\s*,\s*spouse", re.I)
ROW_LIKE = re.compile(r'"[^"\n]*,[^"\n]*"|\t')
# The parser must name the heading: only its HEADER constant line is skipped, the rest of the file is checked.
MEMBER_LIST_LINE = {"backend/agenda_first_notes/names.py": re.compile(r"^HEADER = .*$", re.M)}
SYNTHETIC_TEST_DIRS = ("backend/tests/", "tools/prompt_tuning/data/synthetic/", "tools/prompt_tuning/tests/")
MAX_BYTES = 2 * 1024 * 1024
REQUIRED_FIXTURE_MARKER = "SYNTHETIC"
BINARY_OK = {".png", ".jpg", ".jpeg", ".webp", ".pyc", ".woff", ".woff2", ".ico"}


def _files(staged_only: bool) -> list[Path]:
    # -z: NUL-separated, unquoted paths; without it git quotes non-ASCII names and they would be skipped as missing.
    cmd = (
        ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"]
        if staged_only
        else ["git", "ls-files", "-z"]
    )
    out = subprocess.run(  # noqa: S603
        cmd, cwd=REPO_ROOT, capture_output=True, encoding="utf-8", errors="surrogateescape", check=False
    )
    return [REPO_ROOT / p for p in out.stdout.split("\0") if p]


def _content(path: Path, rel: str, staged: bool) -> bytes | None:
    """The bytes that will be committed: the staged blob with --staged (the working copy may differ)."""
    if staged:
        out = subprocess.run(["git", "show", f":./{rel}"], cwd=REPO_ROOT, capture_output=True, check=False)  # noqa: S603, S607
        return out.stdout if out.returncode == 0 else None
    return path.read_bytes() if path.is_file() else None


def audit_paths(paths: list[Path], staged: bool = False) -> list[str]:
    problems: list[str] = []
    for path in paths:
        rel = path.relative_to(REPO_ROOT).as_posix()
        name = path.name.lower()
        suffix = path.suffix.lower()
        if RUNTIME_FILE.match(name) and not (rel.startswith("frontend/") and name == "settings.json"):  # editor config
            problems.append(f"{rel}: runtime file must not be committed")
        if name.endswith(SUSPICIOUS_SUFFIXES) and not rel.startswith(FIXTURE_DIRS):
            problems.append(f"{rel}: file type {path.suffix} is not allowed in the repository")
        if EXPORT_NAME.search(path.name) or (
            SUSPICIOUS_NAMES.search(path.name)
            and not rel.startswith(NAME_EXEMPT_DIRS)
            and suffix in {".txt", ".md", ".json", ".csv", *SPREADSHEETS, *BINARY_OK}
        ):
            problems.append(
                f"{rel}: name suggests meeting/member data; move it out of the repo or into a synthetic fixtures folder"
            )
        data = _content(path, rel, staged)
        if data is None:  # name checks above still apply to files whose content cannot be read
            continue
        if len(data) > MAX_BYTES:
            problems.append(f"{rel}: larger than 2 MB")
        if suffix in BINARY_OK:
            continue
        try:  # a UTF-16 file (BOM) is decoded too: a spreadsheet export must not skip the content checks
            text = data.decode("utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig")
        except UnicodeDecodeError:
            continue
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                problems.append(f"{rel}: looks like it contains a secret ({pat.pattern[:30]}...)")
                break
        test_file = rel.startswith(SYNTHETIC_TEST_DIRS) or (rel.startswith("frontend/src/") and ".test." in name)
        synthetic_test = test_file and REQUIRED_FIXTURE_MARKER in text
        scanned = MEMBER_LIST_LINE[rel].sub("", text) if rel in MEMBER_LIST_LINE else text
        if not synthetic_test:
            rows = sum(len(_DATE.findall(line)) for line in scanned.splitlines() if ROW_LIKE.search(line))
            if MEMBER_LIST.search(scanned):
                problems.append(f"{rel}: contains a member-list CSV heading; member lists must never be committed")
            elif rows >= 3:
                problems.append(
                    f"{rel}: contains member-list rows (calling dates); member lists must never be committed"
                )
        needs_marker = (rel.startswith(FIXTURE_DIRS) and suffix in FIXTURE_MARKER_SUFFIXES) or (
            rel.startswith(NAME_EXEMPT_DIRS) and suffix in MARKER_SUFFIXES
        )
        if needs_marker and REQUIRED_FIXTURE_MARKER not in text:
            problems.append(
                f"{rel}: fixture/data file lacks the '{REQUIRED_FIXTURE_MARKER}' marker proving it is fictional"
            )
    return problems


def run_audit(staged_only: bool = False) -> int:
    problems = audit_paths(_files(staged_only), staged=staged_only)
    if problems:
        print("Repository audit FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print("Repository audit passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run_audit("--staged" in sys.argv))
