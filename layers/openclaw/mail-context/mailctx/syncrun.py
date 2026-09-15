"""Sync entry point. This is what the systemd timer will call.

    python3 -m mailctx.syncrun --once          # incremental, or initial if new
    python3 -m mailctx.syncrun --max 2000      # bounded validation load
    python3 -m mailctx.syncrun --status
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import preflight
from .auth import AccessTokenProvider, AuthError
from .gmail import GmailReadOnly, TransportError
from .query import MailContext
from .store import Store, connect, migrate
from .sync import DB_PATH, DEFAULT_WINDOW_DAYS, Syncer


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Synchronize the Gmail context index.")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--query", "--exclude", dest="query", default=None,
                    help="extra Gmail search terms applied at listing time, "
                         "ANDed with the window. Exclusions and inclusions "
                         "both work: '-category:promotions -category:social' "
                         "or 'in:sent'. Ids that do not match are never "
                         "fetched, which makes this the cheapest filter there "
                         "is. (--exclude is kept as an alias.)")
    ap.add_argument("--window", type=int, default=DEFAULT_WINDOW_DAYS,
                    help="rolling window in days; 0 means the full mailbox")
    ap.add_argument("--max", type=int, default=None,
                    help="stop after N messages (bounded validation run)")
    ap.add_argument("--concurrency", type=int, default=12)
    ap.add_argument("--full", action="store_true",
                    help="force a complete initial load even if a cursor exists")
    ap.add_argument("--status", action="store_true", help="report index state and exit")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.status:
        if not args.db.exists():
            print("index does not exist yet")
            return 0
        with MailContext(args.db) as ctx:
            res = ctx.status()
            row = res.rows[0]
            print(f"messages       : {row['messages']:,}")
            print(f"threads        : {row['threads']:,}")
            print(f"open candidates: {row['open_candidates']:,}")
            print(f"freshness      : {res.freshness.describe()}")
            print(f"last run       : {row['last_run']}")
            print(f"size on disk   : {args.db.stat().st_size / 1024**2:.1f} MiB")
        return 0

    try:
        pre = preflight.require_for_initial_sync(args.db)
    except preflight.PreflightError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    for c in pre.checks:
        if "WAIVED" in c.detail and not args.quiet:
            print("NOTE: encryption gate waived by operator; index is unencrypted at rest.\n")

    try:
        tokens = AccessTokenProvider()
    except AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    conn = connect(args.db)
    migrate(conn)
    store = Store(conn)
    api = GmailReadOnly(tokens)

    t0 = time.time()
    syncer = Syncer(store, api,
                    window_days=(None if args.window == 0 else args.window),
                    exclude_query=args.query,
                    concurrency=args.concurrency,
                    on_progress=(None if args.quiet else lambda m: print("  ", m, flush=True)))
    try:
        if args.max is not None or args.full:
            res = syncer.initial_sync(max_messages=args.max)
        else:
            res = syncer.sync()
    except TransportError as exc:
        print(f"sync failed: {exc.error_class}", file=sys.stderr)
        return 1

    el = time.time() - t0
    print("\n" + res.summary())
    if syncer.skipped:
        print(f"skipped (already indexed): {syncer.skipped:,}")
    if res.added and el > 0:
        print(f"throughput: {res.added / el:.1f} msg/s")
    if args.db.exists():
        print(f"index size: {args.db.stat().st_size / 1024**2:.1f} MiB")
    return 0 if res.status in ("ok", "skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
