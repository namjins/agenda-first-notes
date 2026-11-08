"""Command line: serve, paths, audit, migrate. Pure Python so it behaves the same on every OS."""

from __future__ import annotations

import argparse
import sys
import webbrowser

from . import config


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agenda-first-notes")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="Start the local application")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true")
    s.add_argument("--reload", action="store_true", help="Developer auto-reload")
    sub.add_parser("paths", help="Show where runtime data is stored")
    sub.add_parser("migrate", help="Create/upgrade workspace databases")
    a = sub.add_parser("audit", help="Scan the repository for files that must not be committed")
    a.add_argument("--staged", action="store_true", help="Only inspect files staged in git")
    args = p.parse_args(argv)

    if args.cmd == "paths":
        d = config.data_dir()
        print(f"Data directory: {d}")
        for wid in ("ward_council", "bishopric"):
            print(f"  {wid}: {d / 'workspaces' / wid}")
        print(f"Settings: {config.settings_path()}")
        return 0
    if args.cmd == "migrate":
        from .main import init_storage

        init_storage()
        print("Databases are up to date.")
        return 0
    if args.cmd == "audit":
        from .repo_audit import run_audit

        return run_audit(staged_only=args.staged)
    if args.cmd == "serve":
        import uvicorn

        from .main import init_storage

        init_storage()
        token = config.api_token()
        url = f"http://127.0.0.1:{args.port}/#token={token}"
        print(
            f"Agenda First Notes is running at:\n\n    {url}\n\nData directory: {config.data_dir()}\nPress Ctrl+C to stop.",
            flush=True,
        )
        if not args.no_browser:
            webbrowser.open(url)
        uvicorn.run(
            "agenda_first_notes.main:create_app",
            factory=True,
            host="127.0.0.1",
            port=args.port,
            reload=args.reload,
            log_level="warning",
            access_log=False,
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
