"""Sync entry point. This is what the systemd timer calls.

    python3 -m calctx.syncrun --once           # incremental, or initial if new
    python3 -m calctx.syncrun --forward 1 --back 0 --once   # bounded validation
    python3 -m calctx.syncrun --status
    python3 -m calctx.syncrun --preflight      # report the gate and exit

The timer makes no model call. It talks to Google and to SQLite, and it is
capable of nothing else.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from mailctx import preflight

from .auth import AuthError, provider, scopes_are_readonly
from .gcal import CalendarReadOnly, TransportError
from .query import CalendarContext
from .store import Store, connect, migrate
from .sync import Syncer
from .timespec import WINDOW_BACK_MONTHS, WINDOW_FORWARD_MONTHS

DB_PATH = Path.home() / ".local/state/calendar-context/calendar-context.sqlite"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Synchronize the Google Calendar context index.")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--back", type=int, default=WINDOW_BACK_MONTHS,
                    help="months of history to index")
    ap.add_argument("--forward", type=int, default=WINDOW_FORWARD_MONTHS,
                    help="months ahead to index")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--once", action="store_true", help="run one sync (the default action)")
    ap.add_argument("--status", action="store_true", help="report index state and exit")
    ap.add_argument("--preflight", action="store_true", help="report the safety gate and exit")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.preflight:
        print(preflight.run(args.db).report())
        return 0 if preflight.run(args.db).ok else 3

    if args.status:
        if not args.db.exists():
            print("index does not exist yet")
            return 0
        with CalendarContext(args.db) as ctx:
            res = ctx.status()
            row = res.rows[0]
            print(f"calendars      : {row['calendars']:,}")
            print(f"events (series): {row['events']:,} ({row['series']:,} recurring)")
            print(f"instances      : {row['instances']:,} ({row['cancelled']:,} cancelled)")
            print(f"freshness      : {res.freshness.describe()}")
            print(f"window         : {row['last_run']}")
            if row["encryption_waiver"]["active"]:
                print(f"WAIVER         : {row['encryption_waiver']['reason']}")
            print(f"size on disk   : {args.db.stat().st_size / 1024**2:.1f} MiB")
        return 0

    # The gate is checked before any real calendar data can be stored. A prose
    # requirement in a runbook does not stop a timer from firing; this does.
    try:
        pre = preflight.require_for_initial_sync(args.db)
    except preflight.PreflightError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    for c in pre.checks:
        if "WAIVED" in c.detail and not args.quiet:
            print("NOTE: encryption gate waived by operator; index is unencrypted at rest.\n")

    try:
        tokens = provider()
    except AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # A stored token outlives the code that requested it. If someone re-consents
    # with a wider grant, refuse rather than quietly operate under it.
    grant = tokens.status()
    if grant.get("authorized") and not scopes_are_readonly(tuple(grant.get("scopes", ()))):
        print("error: stored grant is not Calendar-readonly; re-authorize", file=sys.stderr)
        return 2

    conn = connect(args.db)
    migrate(conn)
    store = Store(conn)
    api = CalendarReadOnly(tokens)

    t0 = time.time()
    syncer = Syncer(store, api, back_months=args.back, forward_months=args.forward,
                    concurrency=args.concurrency,
                    on_progress=(None if args.quiet else lambda m: print("  ", m, flush=True)))
    try:
        res = syncer.sync()
    except TransportError as exc:
        print(f"sync failed: {exc.error_class}", file=sys.stderr)
        return 1

    el = time.time() - t0
    print("\n" + res.summary())
    for c in res.per_calendar:
        if c.status != "ok":
            print(f"  calendar failed ({c.error_class}) -- others unaffected")
    if res.skipped:
        print(f"series skipped (already expanded): {res.skipped:,}")
    if res.instances and el > 0:
        print(f"throughput: {res.instances / el:.1f} instances/s")
    if args.db.exists():
        print(f"index size: {args.db.stat().st_size / 1024**2:.1f} MiB")
    return 0 if res.status in ("ok", "partial") else 1


if __name__ == "__main__":
    raise SystemExit(main())
