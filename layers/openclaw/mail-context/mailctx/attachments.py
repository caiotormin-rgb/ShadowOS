"""Attachment presence, told truthfully.

`mail_messages.has_attachments` used to answer this question and it lied: the
sync fetches `format=metadata`, which returns no MIME parts, so the MIME walk in
`sync.parse_message` never fires and the column read 0 for all 132,588 indexed
messages. A caller asking "which mail has files" got `nothing found` and no
indication that nothing had ever been checked. The column is gone (schema v3);
this module is what replaced it.

The replacement is three-valued, because two values cannot express what we know:

    'yes'      Gmail's search index (a hint) or a stored MIME part says so
    'no'       targeting covered this message and Gmail did not list it
    'unknown'  nobody has ever asked Gmail about this message

Coverage has two edges and both matter:

  * `covered_from_ts` -- targeting runs accept a `window_start_ts` and pass it
    to Gmail as `after:`. A run that only asked about the last 24 months proves
    nothing about 2008, so older messages stay 'unknown'.
  * `covered_until_synced_at` -- a run can only have seen messages the index
    already held. A message ingested after the last run was never asked about,
    however old its `internal_ts` is. `synced_at` is stamped by our own store,
    so it is the right clock for "had we got this yet".

Only runs that refreshed the `any` hint (`has:attachment`) count as coverage.
A run that refreshed only `pdf` cannot turn 'unknown' into 'no'.

Stdlib only, and no import cost beyond sqlite3 -- this is on the agent query
path and the budget is 100 ms end to end.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# The one place the SQL for the derived views lives, so a database that predates
# schema v3 (production, and every snapshot taken from it) can be given the same
# views as a TEMP overlay instead of a second, drifting implementation.
COVERAGE_VIEW_SQL = """
  SELECT
    (SELECT COUNT(*) FROM mail_targeting_runs
      WHERE status = 'ok' AND covers_presence = 1)                       AS runs,
    (SELECT COALESCE(MIN(COALESCE(covered_from_ts, 0)), 0)
       FROM mail_targeting_runs
      WHERE status = 'ok' AND covers_presence = 1)                       AS covered_from_ts,
    (SELECT COALESCE(MAX(finished_at), 0) FROM mail_targeting_runs
      WHERE status = 'ok' AND covers_presence = 1)                       AS covered_until_synced_at,
    (SELECT MAX(finished_at) FROM mail_targeting_runs
      WHERE status = 'ok' AND covers_presence = 1)                       AS last_run_at
"""

STATE_VIEW_SQL = """
  SELECT m.message_id, m.thread_id, m.internal_ts, m.synced_at, m.deleted_at,
         CASE
           WHEN EXISTS (SELECT 1 FROM mail_attachment_hints h
                         WHERE h.message_id = m.message_id)
             OR EXISTS (SELECT 1 FROM mail_attachments a
                         WHERE a.message_id = m.message_id)          THEN 'yes'
           WHEN c.runs = 0
             OR m.internal_ts < c.covered_from_ts
             OR m.synced_at  > c.covered_until_synced_at              THEN 'unknown'
           ELSE 'no'
         END AS attachment_state
    FROM mail_messages m CROSS JOIN mail_targeting_coverage c
"""

# An empty stand-in used when the real table does not exist yet. Same shape, no
# rows -- so coverage comes out as "never ran" and every message reads 'unknown',
# which is the correct answer for a database that has never been targeted.
_RUNS_TABLE_SQL = """
  CREATE TEMP TABLE mail_targeting_runs (
    run_id INTEGER PRIMARY KEY, started_at INTEGER, finished_at INTEGER,
    window_start_ts INTEGER, covered_from_ts INTEGER,
    covers_presence INTEGER NOT NULL DEFAULT 0,
    hints TEXT, rows_written INTEGER, status TEXT, error_class TEXT)
"""

UNKNOWN, YES, NO = "unknown", "yes", "no"


@dataclass(frozen=True)
class Coverage:
    """What attachment targeting can currently speak for."""

    runs: int = 0
    covered_from_ts: int = 0
    covered_until_synced_at: int = 0
    last_run_at: int | None = None

    @property
    def ever_ran(self) -> bool:
        return self.runs > 0

    def state_for(self, *, has_evidence: bool, internal_ts: int, synced_at: int) -> str:
        """The same rule the SQL view applies. tests/test_attachments.py asserts
        the two agree on a fixture matrix, so this cannot quietly drift."""
        if has_evidence:
            return YES
        if (not self.ever_ran
                or internal_ts < self.covered_from_ts
                or synced_at > self.covered_until_synced_at):
            return UNKNOWN
        return NO

    def describe(self) -> str:
        if not self.ever_ran:
            return ("attachment targeting has never run: attachment presence is "
                    "UNKNOWN for every message, not 'none'")
        return (f"attachment targeting last ran at {self.last_run_at}; messages "
                f"older than internal_ts {self.covered_from_ts} or synced after "
                f"that run are still unknown")


def _has(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? "
        "UNION ALL SELECT 1 FROM temp.sqlite_master WHERE name = ?",
        (name, name)).fetchone()
    return row is not None


def ensure_views(conn: sqlite3.Connection) -> None:
    """Make the derived views usable on any database, including a read-only one.

    Databases created at schema v3 already carry them. Production and every
    snapshot taken before the migration do not, and a snapshot is opened
    `mode=ro` so it can never be given them permanently -- but TEMP objects live
    in a separate, always-writable database and shadow `main` by name, so the
    same SQL works everywhere. Idempotent.
    """
    if not _has(conn, "mail_targeting_runs"):
        conn.execute(_RUNS_TABLE_SQL)
    if not _has(conn, "mail_targeting_coverage"):
        conn.execute("CREATE TEMP VIEW mail_targeting_coverage AS" + COVERAGE_VIEW_SQL)
    if not _has(conn, "mail_message_attachments"):
        conn.execute("CREATE TEMP VIEW mail_message_attachments AS" + STATE_VIEW_SQL)


def coverage(conn: sqlite3.Connection) -> Coverage:
    ensure_views(conn)
    row = conn.execute(
        "SELECT runs, covered_from_ts, covered_until_synced_at, last_run_at "
        "FROM mail_targeting_coverage").fetchone()
    if row is None:
        return Coverage()
    runs, frm, until, last = tuple(row)
    return Coverage(int(runs or 0), int(frm or 0), int(until or 0), last)
