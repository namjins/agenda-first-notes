#!/usr/bin/env python3
"""One-command launcher (cross-platform, pure Python).

    python run.py            # build the frontend once if needed, then start the local app
    python run.py --port 9000

Requires: Python 3.12+, uv (https://docs.astral.sh/uv/), Node.js 20+ (only for the first build),
and a locally running Ollama with at least one model.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def need(cmd: str, hint: str) -> str:
    path = shutil.which(cmd)
    if not path:
        sys.exit(f"'{cmd}' not found. {hint}")
    return path


def main() -> None:
    uv = need("uv", "Install uv: https://docs.astral.sh/uv/getting-started/installation/")
    dist = ROOT / "frontend" / "dist" / "index.html"
    if not dist.exists():
        npm = need("npm", "Install Node.js 20+ (needed once to build the user interface): https://nodejs.org")
        print("Building the user interface (first run only)...")
        subprocess.run([npm, "ci"], cwd=ROOT / "frontend", check=True)
        subprocess.run([npm, "run", "build"], cwd=ROOT / "frontend", check=True)
    subprocess.run([uv, "sync", "--directory", str(ROOT / "backend"), "--all-extras", "--quiet"], check=True)
    cmd = [uv, "run", "--directory", str(ROOT / "backend"), "agenda-first-notes", "serve", *sys.argv[1:]]
    try:
        subprocess.run(cmd, check=False)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
