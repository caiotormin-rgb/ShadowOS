#!/usr/bin/env python3
"""Blank grocery member text past its retention period, and report counts.

The engine already does this whenever it opens a list (retention.py lists the
fields: message text and transcripts, and the note copies history keeps). This
script is for doing it on demand — after a deploy, from cron, or to check — and
it never depends on a scheduler existing.

    python3 tools/grocery-list/core/scripts/purge_raw_text.py [DB ...] [--private-dir DIR] [--dry-run]
                                                   [--busy-timeout SECONDS]

With no database and no --private-dir it uses GROCERY_DB, else
data/grocery.sqlite3. `--private-dir` adds every private per-requester list in
that directory (the plugin's `privateDbDir`: `*.sqlite3` files directly in it).
The retention period is 90 days, or GROCERY_RAW_TEXT_RETENTION_DAYS (a whole
number, 1-3650).

Output is JSON with counts and timestamps only — never member text, never a
phone number. For exactly one database it is that database's report; for more,
or with --private-dir, it is {"databases": [report, ...]}. A database that
could not be processed gets {"database": label, "error": ...} instead of counts,
and the run carries on with the rest: one bad file must not leave every list
after it unpurged on every cron run. For a failure (a file that is not a
database, an empty file, a lock held past --busy-timeout, an I/O error) the
error is the exception's class name only — never its message, which can carry
a path. A private list's file name is a hash of its requester's phone number,
which is guessable, so private lists are reported as `<private list N>`, never
by name or directory.

--busy-timeout (seconds, default 30, at most 600) bounds how long each database
waits for another process's lock before it is reported as an OperationalError
and skipped. The engine's own purge waits at most a second (retention.py).

Exit status; when several happen, the highest code wins (4 over 3 over 2):

    0  done (or, with --dry-run, reported)
    2  refused, nothing opened: no such file, not a regular file, not run by
       the database file's owner, an invalid retention setting, or a
       --private-dir that is not a directory
    3  purged, but the WAL checkpoint could not finish because another
       connection is still reading; the blanked text's old pages may remain in
       the -wal file until that reader ends. Run it again later.
    4  one or more databases failed (see their "error" entries); every other
       database was still processed

It must run as the user that owns the database file — `openclaw`, never root.
SQLite creates the -wal and -shm files as whoever opens the database, and
root-owned ones stop the gateway's engine processes from opening the list.

`--dry-run` reads without writing. When a database has no -wal file it reads
the main file as immutable, so it creates no -wal or -shm either; when one
already exists (the gateway has the list open) it reads through it and leaves
it alone — deleting a -wal someone else created could lose committed data.

A real run upgrades the schema if it is behind (as any open does), purges, and
checkpoints the WAL (TRUNCATE) so the old pages do not linger in the -wal.

Daily from cron (as openclaw), family list plus every private list:

    17 4 * * * cd /home/openclaw/.openclaw/workspace && python3 tools/grocery-list/core/scripts/purge_raw_text.py tools/grocery-list/core/data/grocery.sqlite3 --private-dir <privateDbDir> >/dev/null
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))

import db  # noqa: E402
import retention  # noqa: E402
from errors import GroceryError  # noqa: E402

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_CHECKPOINT_INCOMPLETE = 3
EXIT_FAILED = 4
DEFAULT_BUSY_TIMEOUT = 30.0
MAX_BUSY_TIMEOUT = 600.0
# The checkpoint waits this long for readers, not connect()'s 30 s.
CHECKPOINT_BUSY_MS = 2000


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def field_state(conn: sqlite3.Connection, field: retention.Field) -> dict:
    if field.column not in _columns(conn, field.table):   # predates the history feature
        return {"with_text": 0, "oldest_with_text": None, "newest_with_text": None}
    order = (f"SELECT {field.stamp} FROM {field.table} WHERE {field.column} != '' "
             f"ORDER BY unixepoch({field.stamp})")
    oldest = conn.execute(f"{order} ASC LIMIT 1").fetchone()
    newest = conn.execute(f"{order} DESC LIMIT 1").fetchone()
    return {
        "with_text": conn.execute(
            f"SELECT COUNT(*) FROM {field.table} WHERE {field.column} != ''").fetchone()[0],
        "oldest_with_text": oldest[0] if oldest else None,
        "newest_with_text": newest[0] if newest else None,
    }


def snapshot(conn: sqlite3.Connection, now: datetime, days: int) -> dict:
    fields = {name: field_state(conn, field) for name, field in retention.FIELDS.items()}
    complete = all(f.column in _columns(conn, f.table) for f in retention.FIELDS.values())
    due = retention.due(conn, now, days) if complete else {name: 0 for name in fields}
    for name, count in due.items():
        fields[name]["due"] = count
    tables = dict.fromkeys(f.table for f in retention.FIELDS.values())
    return {
        "version": conn.execute("PRAGMA user_version").fetchone()[0],
        "fields": fields,
        "table_rows": {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                       for t in tables},
    }


def read_only(path: Path, timeout: float) -> sqlite3.Connection:
    """A connection that writes nothing and leaves no files it did not find.

    The path is percent-encoded: in a URI, '#' and '?' would end the filename.
    """
    params = "mode=ro"
    if not Path(f"{path}-wal").exists():
        # No WAL, so everything committed is in the main file. immutable=1 reads
        # it without creating -wal or -shm; a root- or cron-owned pair of those
        # beside the live list is exactly what stops the bot (QA M1).
        params += "&immutable=1"
    return sqlite3.connect(f"file:{quote(str(path))}?{params}", uri=True, timeout=timeout)


def refusal(path: Path, label: str) -> str | None:
    """Why this database must not be opened, naming it only by `label`."""
    if not path.exists():
        return f"no database at {label}"
    if not path.is_file():
        return f"not a file: {label}"
    owner = path.stat().st_uid
    if os.geteuid() != owner:
        return (f"run this as the owner of {label} (uid {owner}), not uid "
                f"{os.geteuid()}: SQLite would create -wal/-shm files the gateway "
                f"cannot open")
    return None


def process(path: Path, label: str, days: int, now: datetime,
            dry_run: bool, timeout: float) -> tuple[int, dict]:
    """Report on, and unless dry_run purge, one database. Prints nothing.

    The read comes first and fails on a file that is not a grocery database, so
    a garbage or empty file is never opened for writing — which would turn an
    empty file into a fresh, empty list.
    """
    problem = refusal(path, label)
    if problem:
        return EXIT_REFUSED, {"database": label, "error": problem}

    reader = read_only(path, timeout)
    try:
        before = snapshot(reader, now, days)
    finally:
        reader.close()

    purged, checkpoint, after = None, None, before
    if not dry_run:
        conn = db.connect(path, purge=False, timeout=timeout)
        try:
            purged = retention.purge(conn, now=now, days=days)
            conn.execute(f"PRAGMA busy_timeout = {CHECKPOINT_BUSY_MS}")
            busy, _, _ = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            checkpoint = {"complete": not busy}
            after = snapshot(conn, now, days)
        finally:
            conn.close()

    report = {
        "database": label,
        "retention_days": days,
        "cutoff": retention.cutoff(now, days).isoformat(),
        "dry_run": dry_run,
        "schema_version": {"before": before["version"], "after": after["version"]},
        "table_rows": before["table_rows"],
        "fields": {
            name: {
                "with_text_before": before["fields"][name]["with_text"],
                "due": before["fields"][name]["due"],
                "purged": None if purged is None else purged[name],
                "with_text_after": after["fields"][name]["with_text"],
                "oldest_with_text_before": before["fields"][name]["oldest_with_text"],
                "newest_with_text_before": before["fields"][name]["newest_with_text"],
                "oldest_with_text_after": after["fields"][name]["oldest_with_text"],
                "newest_with_text_after": after["fields"][name]["newest_with_text"],
            }
            for name in retention.FIELDS
        },
        "wal_checkpoint": checkpoint,
    }
    if checkpoint is not None and not checkpoint["complete"]:
        return EXIT_CHECKPOINT_INCOMPLETE, report
    return EXIT_OK, report


def busy_timeout(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number of seconds") from None
    if not 0 < seconds <= MAX_BUSY_TIMEOUT:      # also false for nan
        raise argparse.ArgumentTypeError(
            f"must be more than 0 and at most {MAX_BUSY_TIMEOUT:g} seconds")
    return seconds


def targets(databases: list[str], private_dir: str | None) -> list[tuple[Path, str]]:
    found = [(Path(d).expanduser().resolve(), None) for d in databases]
    found = [(path, str(path)) for path, _ in found]
    if private_dir is not None:
        directory = Path(private_dir).expanduser().resolve()
        lists = sorted(p for p in directory.iterdir()
                       if p.suffix == ".sqlite3" and p.is_file())
        found += [(path, f"<private list {n}>")
                  for n, path in enumerate(lists, start=1)]
    return found


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("databases", nargs="*", metavar="DB")
    parser.add_argument("--private-dir",
                        help="also purge every private list (*.sqlite3) in this directory")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what is due without changing anything")
    parser.add_argument("--busy-timeout", type=busy_timeout, default=DEFAULT_BUSY_TIMEOUT,
                        metavar="SECONDS",
                        help=f"wait at most this long for a lock per database "
                             f"(default {DEFAULT_BUSY_TIMEOUT:g}, at most {MAX_BUSY_TIMEOUT:g})")
    args = parser.parse_args(argv[1:])

    if args.private_dir is not None and not Path(args.private_dir).expanduser().is_dir():
        print(f"error: --private-dir is not a directory: {args.private_dir}", file=sys.stderr)
        return EXIT_REFUSED
    try:
        days = retention.retention_days()
    except GroceryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    single = len(args.databases) <= 1 and args.private_dir is None
    databases = args.databases or ([] if args.private_dir is not None else [str(db.DEFAULT_DB)])
    now = datetime.now(timezone.utc)

    code, reports = EXIT_OK, []
    for path, label in targets(databases, args.private_dir):
        try:
            result, report = process(path, label, days, now, args.dry_run, args.busy_timeout)
        except (sqlite3.Error, OSError) as exc:
            # The class name only: messages can carry a path or a hashed name.
            result, report = EXIT_FAILED, {"database": label, "error": type(exc).__name__}
        code = max(code, result)
        reports.append(report)
        if result == EXIT_FAILED:
            print(f"error: {label}: {report['error']}", file=sys.stderr)
        elif result == EXIT_REFUSED:
            print(f"error: {report['error']}", file=sys.stderr)
        elif result == EXIT_CHECKPOINT_INCOMPLETE:
            print(f"error: {label}: purged, but the WAL checkpoint did not complete "
                  f"(another connection is reading); run again later", file=sys.stderr)

    if single:
        if "error" not in reports[0]:
            print(json.dumps(reports[0], indent=2))
    else:
        print(json.dumps({"databases": reports}, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
