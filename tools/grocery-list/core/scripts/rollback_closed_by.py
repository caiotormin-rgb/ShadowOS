#!/usr/bin/env python3
"""Clear trips.closed_by before going back to the previous engine.

    python3 tools/grocery-list/core/scripts/rollback_closed_by.py path/to/grocery.sqlite3 --yes
    python3 tools/grocery-list/core/scripts/rollback_closed_by.py path/to/grocery.sqlite3 --yes --reset-version

Why (QA H2): the migration fills trips.closed_by with the closer's actor, which
is their phone number. The new engine's `history` prints only a masked label.
The previous engine's `history` returns every trips column verbatim, so on a
migrated database it would hand household members, over WhatsApp, the raw
phone number of whoever closed each trip. Clearing the column restores what
that engine expects: nothing there. The column itself stays; the old engine
ignores columns it does not know.

Before running it:

- **Pause the gateway.** Nothing should be writing to the list while this runs,
  and the old engine must not serve `history` between the downgrade and this.
- **Take a backup first**, and **run it as the `openclaw` user, never root**
  (SQLite creates -wal/-shm files owned by whoever opens the database).

Rolling forward again later: re-deploying the new engine does NOT refill
closed_by by itself, because the schema version says the migration already
ran. Either pass --reset-version now, or set `PRAGMA user_version = 0` before
running scripts/pre_migrate.py; the (idempotent) migration then recovers the
closers from the event log.

Prints counts only — never an actor or a phone number. Plain sqlite3, not
db.connect, so it never migrates anything itself.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clear trips.closed_by (see module docstring).")
    parser.add_argument("database")
    parser.add_argument("--yes", action="store_true",
                        help="required: confirms the gateway is paused and a backup exists")
    parser.add_argument("--reset-version", action="store_true",
                        help="also set user_version = 0, so the next open re-runs the migration")
    args = parser.parse_args(argv)

    path = Path(args.database).expanduser().resolve()
    if not path.exists():
        print(f"error: no database at {path}", file=sys.stderr)
        return 2
    if not args.yes:
        print("error: refusing to change the database without --yes "
              "(pause the gateway and take a backup first)", file=sys.stderr)
        return 2

    conn = sqlite3.connect(path, timeout=30)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(trips)")}
        version_before = conn.execute("PRAGMA user_version").fetchone()[0]
        total, with_closer = (conn.execute("SELECT COUNT(*), COUNT(closed_by) FROM trips").fetchone()
                              if "closed_by" in columns else
                              (conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0], 0))
        with conn:
            cleared = (conn.execute("UPDATE trips SET closed_by = NULL WHERE closed_by IS NOT NULL")
                       .rowcount if "closed_by" in columns else 0)
            if args.reset_version:
                conn.execute("PRAGMA user_version = 0")
        version_after = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()

    print(json.dumps({
        "database": str(path),
        "trips": total,
        "closers_before": with_closer,
        "closers_cleared": cleared,
        "schema_version": {"before": version_before, "after": version_after},
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
