#!/usr/bin/env python3
"""Run the schema upgrade now, instead of inside the first request after a merge.

Opens an existing grocery database once through db.connect — which migrates it
if its schema version is behind — and prints the version and columns before
and after. Safe to run twice: the second run reports nothing to do.

    python3 tools/grocery-list/core/scripts/pre_migrate.py [path/to/grocery.sqlite3]

With no path it uses GROCERY_DB, else data/grocery.sqlite3. It refuses to
create a database that does not exist, so a typo cannot conjure an empty list.

Before running it:

- **Take a backup first.** The upgrade is one transaction and is tested, but
  it rewrites the schema of the live list; a copy costs nothing. Copy the
  database while nothing is writing to it (pause the gateway), or copy the
  -wal and -shm files alongside it.
- **Run it as the `openclaw` user, never as root.** SQLite creates the -wal
  and -shm files as whoever opens the database. Root-owned ones cannot be
  opened by the gateway's engine processes, and the bot stops answering.

To roll forward after scripts/rollback_closed_by.py, reset the version first
so the closers are recovered from the log again — the rollback script's
--reset-version does exactly that.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))

import db  # noqa: E402


def schema(conn: sqlite3.Connection) -> tuple[int, set[tuple[str, str]]]:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    columns = {(table, r[1]) for table in tables
               for r in conn.execute(f"PRAGMA table_info({table})")}
    return version, columns


def main(argv: list[str]) -> int:
    path = Path(argv[1]).expanduser().resolve() if len(argv) > 1 else db.DEFAULT_DB
    if not path.exists():
        print(f"error: no database at {path}", file=sys.stderr)
        return 2

    before_conn = sqlite3.connect(path)
    before_version, before_columns = schema(before_conn)
    before_conn.close()

    conn = db.connect(path)
    after_version, after_columns = schema(conn)
    closers = conn.execute(
        "SELECT COUNT(*), COUNT(closed_by) FROM trips").fetchone()
    conn.close()

    print(json.dumps({
        "database": str(path),
        "schema_version": {"before": before_version, "after": after_version,
                           "target": db.SCHEMA_VERSION},
        "migrated": before_version < after_version,
        "columns_added": sorted(f"{t}.{c}" for t, c in after_columns - before_columns),
        "trips": {"total": closers[0], "with_closer": closers[1]},
    }, indent=2))
    return 0 if after_version >= db.SCHEMA_VERSION else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
