"""Sync entry point. This is what the systemd timer will call.

    python3 -m drivectx.syncrun --once             # incremental, or initial if new
    python3 -m drivectx.syncrun --max 2000         # bounded validation load
    python3 -m drivectx.syncrun --recheck <id>     # explicit live re-check
    python3 -m drivectx.syncrun --status

Requires mail-context on PYTHONPATH: the encryption preflight and the OAuth
broker both live there, deliberately, and are imported rather than copied.

Logs carry ids, counts, durations and error classes. Never a file name, a
folder name, an owner, an address or a link -- tests/test_log_redaction.py
walks this module's AST to make sure.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from mailctx import preflight

from .auth import AuthError, provider
from .drive import DriveReadOnly, TransportError
from .query import DriveContext
from .store import Store, connect, migrate
from .sync import Syncer

DB_PATH = Path.home() / ".local/state/drive-context/drive-context.sqlite"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Synchronize the Google Drive metadata index.")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--max", type=int, default=None,
                    help="stop after N items (bounded validation run)")
    ap.add_argument("--pages", type=int, default=None,
                    help="stop after N listing pages")
    ap.add_argument("--full", action="store_true",
                    help="force a complete initial listing even if a cursor exists")
    ap.add_argument("--recheck", nargs="+", metavar="FILE_ID", default=None,
                    help="re-fetch specific file ids now")
    ap.add_argument("--include-shared-drives", action="store_true",
                    help="index items carrying a driveId (default: excluded)")
    ap.add_argument("--concurrency", type=int, default=8,
                    help="workers for the --recheck path only")
    ap.add_argument("--status", action="store_true", help="report index state and exit")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.status:
        if not args.db.exists():
            print("index does not exist yet")
            return 0
        with DriveContext(args.db) as ctx:
            res = ctx.status()
            row = res.rows[0]
            print(f"files          : {row['files']:,} ({row['folders']:,} folders)")
            print(f"trashed        : {row['trashed']:,}")
            print(f"tombstoned     : {row['tombstoned']:,}")
            print(f"parent edges   : {row['parent_edges']:,}")
            print(f"initial listing: {'complete' if row['initial_complete'] else 'INCOMPLETE'}")
            print("sharing        : " + ", ".join(
                f"{state}={count}" for state, count in row["sharing"].items()))
            print(f"freshness      : {res.freshness.describe()}")
            print(f"last run       : {row['last_run']}")
            print(f"size on disk   : {args.db.stat().st_size / 1024**2:.1f} MiB")
            if row["encryption_waiver"]:
                print("NOTE: encryption gate waived by operator; index is unencrypted at rest.")
        return 0

    try:
        pre = preflight.require_for_initial_sync(args.db)
    except preflight.PreflightError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    for c in pre.checks:
        if "WAIVED" in c.detail and not args.quiet:
            print("NOTE: encryption gate waived by operator; index is unencrypted at rest.\n")

    if args.include_shared_drives and not args.quiet:
        print("NOTE: shared drives are being indexed; the default is to exclude them.\n")

    try:
        tokens = provider()
    except AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    conn = connect(args.db)
    migrate(conn)
    store = Store(conn)
    api = DriveReadOnly(tokens, include_shared_drives=args.include_shared_drives)

    t0 = time.time()
    syncer = Syncer(store, api,
                    include_shared_drives=args.include_shared_drives,
                    concurrency=args.concurrency,
                    on_progress=(None if args.quiet else lambda m: print("  ", m, flush=True)))
    try:
        if args.recheck:
            res = syncer.refresh_files(args.recheck)
        elif args.max is not None or args.pages is not None or args.full:
            res = syncer.initial_sync(max_files=args.max, max_pages=args.pages)
        else:
            res = syncer.sync()
    except TransportError as exc:
        print(f"sync failed: {exc.error_class}", file=sys.stderr)
        return 1

    el = time.time() - t0
    print("\n" + res.summary())
    if syncer.skipped:
        print(f"skipped (already indexed): {syncer.skipped:,}")
    if not res.complete:
        print("listing incomplete: bounded by --max/--pages or by the resync budget")
    if res.added and el > 0:
        print(f"throughput: {res.added / el:.1f} items/s")
    if args.db.exists():
        print(f"index size: {args.db.stat().st_size / 1024**2:.1f} MiB")
    return 0 if res.status in ("ok", "skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
