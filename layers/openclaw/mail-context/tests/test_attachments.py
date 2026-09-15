"""Attachment presence must never be confidently wrong.

The defect these tests exist for: `mail_messages.has_attachments` read 0 for all
132,588 indexed messages because the sync fetches format=metadata and the MIME
walk that would have set it never fires. Every query trusting the column got
"no attachments" when the truth was "nobody ever looked".
"""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from mailctx import SCHEMA_VERSION, attachments
from mailctx.store import SCHEMA_PATH, connect, migrate
from tests.fixtures import memory_conn, msg


def _seed(conn, mid, internal_ts, synced_at):
    conn.execute("INSERT OR IGNORE INTO mail_threads (thread_id, message_count, synced_at)"
                 " VALUES (?, 1, ?)", (f"t-{mid}", synced_at))
    conn.execute("""INSERT INTO mail_messages (message_id, thread_id, internal_ts,
                        synced_at, from_addr) VALUES (?,?,?,?,'a@x.invalid')""",
                 (mid, f"t-{mid}", internal_ts, synced_at))


def _run(conn, *, covered_from, finished_at, status="ok", covers=1):
    conn.execute("""INSERT INTO mail_targeting_runs
                      (started_at, window_start_ts, covered_from_ts, covers_presence,
                       hints, rows_written, status, finished_at)
                    VALUES (?,?,?,?,?,0,?,?)""",
                 (finished_at, covered_from, covered_from, covers,
                  json.dumps(["any"]), status, finished_at))


class DerivationTest(unittest.TestCase):
    """The SQL view and the Python rule must agree, always.

    Two implementations exist because the query path wants one cheap fact per
    instance while SQL callers want a joinable column. Two implementations of a
    truthfulness rule are worth having only if something proves they agree.
    """

    MATRIX = [
        # (internal_ts, synced_at, hinted)
        (100, 10, False), (100, 10, True),
        (500, 10, False), (500, 10, True),
        (500, 900, False), (5000, 10, False),
        (0, 0, False),
    ]

    def setUp(self):
        self.conn = memory_conn()
        migrate(self.conn)

    def _compare(self):
        cov = attachments.coverage(self.conn)
        for row in self.conn.execute(
                "SELECT message_id, internal_ts, synced_at, attachment_state "
                "FROM mail_message_attachments"):
            hinted = self.conn.execute(
                "SELECT 1 FROM mail_attachment_hints WHERE message_id=?",
                (row["message_id"],)).fetchone() is not None
            stored = self.conn.execute(
                "SELECT 1 FROM mail_attachments WHERE message_id=?",
                (row["message_id"],)).fetchone() is not None
            py = cov.state_for(has_evidence=hinted or stored,
                               internal_ts=row["internal_ts"],
                               synced_at=row["synced_at"])
            self.assertEqual(py, row["attachment_state"],
                             f"SQL and Python disagree on {row['message_id']}")

    def _load_matrix(self):
        for i, (its, sat, hinted) in enumerate(self.MATRIX):
            mid = f"m{i}"
            _seed(self.conn, mid, its, sat)
            if hinted:
                self.conn.execute(
                    "INSERT INTO mail_attachment_hints (message_id, hint, synced_at)"
                    " VALUES (?, 'any', 0)", (mid,))

    def test_sql_and_python_agree_with_no_targeting_run(self):
        self._load_matrix()
        self._compare()
        states = {r[0] for r in self.conn.execute(
            "SELECT attachment_state FROM mail_message_attachments")}
        self.assertEqual(states, {"unknown", "yes"})

    def test_sql_and_python_agree_with_a_windowed_run(self):
        self._load_matrix()
        _run(self.conn, covered_from=400, finished_at=100)
        self._compare()

    def test_sql_and_python_agree_with_a_full_run(self):
        self._load_matrix()
        _run(self.conn, covered_from=None, finished_at=10 ** 9)
        self._compare()

    def test_no_is_only_reachable_inside_coverage(self):
        _seed(self.conn, "old", 100, 10)
        _seed(self.conn, "inside", 500, 10)
        _seed(self.conn, "fresh", 500, 900)
        _run(self.conn, covered_from=400, finished_at=100)
        got = {r["message_id"]: r["attachment_state"] for r in self.conn.execute(
            "SELECT message_id, attachment_state FROM mail_message_attachments")}
        self.assertEqual(got, {"old": "unknown", "inside": "no", "fresh": "unknown"})

    def test_running_and_failed_runs_are_not_coverage(self):
        _seed(self.conn, "m1", 500, 10)
        _run(self.conn, covered_from=None, finished_at=10 ** 9, status="running")
        _run(self.conn, covered_from=None, finished_at=10 ** 9, status="failed")
        self.assertFalse(attachments.coverage(self.conn).ever_ran)
        self.assertEqual(self.conn.execute(
            "SELECT attachment_state FROM mail_message_attachments").fetchone()[0],
            "unknown")

    def test_describe_says_unknown_not_none(self):
        text = attachments.Coverage().describe()
        self.assertIn("UNKNOWN", text)
        self.assertIn("never run", text)


class MigrationTest(unittest.TestCase):
    def test_v2_database_loses_the_lying_column_and_keeps_its_rows(self):
        conn = memory_conn()
        v2 = (SCHEMA_PATH.read_text()
              .replace("  -- No has_attachments here by design; see the v3 note at the top of the file.\n",
                       "  has_attachments  INTEGER NOT NULL DEFAULT 0,\n"))
        # Drop the v3-only objects so this really is a pre-migration database.
        v2 = v2.split("CREATE TABLE IF NOT EXISTS mail_targeting_runs")[0]
        conn.executescript(v2)
        conn.execute("PRAGMA user_version = 2")
        _seed(conn, "m1", 100, 10)
        self.assertIn("has_attachments",
                      {r[1] for r in conn.execute("PRAGMA table_info(mail_messages)")})

        migrate(conn)

        self.assertNotIn("has_attachments",
                         {r[1] for r in conn.execute("PRAGMA table_info(mail_messages)")})
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        self.assertEqual(conn.execute(
            "SELECT count(*) FROM mail_messages").fetchone()[0], 1, "rows survive")
        self.assertEqual(conn.execute(
            "SELECT attachment_state FROM mail_message_attachments").fetchone()[0],
            "unknown")

    def test_fts_still_works_after_the_column_is_dropped(self):
        """DROP COLUMN would fail, or the triggers would break, if the FTS
        triggers named the column. They do not -- prove it rather than assume."""
        conn = memory_conn()
        migrate(conn)
        _seed(conn, "m1", 100, 10)
        conn.execute("UPDATE mail_messages SET subject='escritura exemplo'"
                     " WHERE message_id='m1'")
        hit = conn.execute(
            "SELECT rowid FROM mail_search WHERE mail_search MATCH 'exemplo'").fetchone()
        self.assertIsNotNone(hit)


class ReadOnlyOverlayTest(unittest.TestCase):
    """A snapshot predates the migration, is opened mode=ro, and can never be
    given the views permanently. It must still answer truthfully."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "snap.sqlite"
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        v2 = (SCHEMA_PATH.read_text()
              .replace("  -- No has_attachments here by design; see the v3 note at the top of the file.\n",
                       "  has_attachments  INTEGER NOT NULL DEFAULT 0,\n"))
        v2 = v2.split("CREATE TABLE IF NOT EXISTS mail_targeting_runs")[0]
        conn.executescript(v2)
        conn.execute("PRAGMA user_version = 2")
        _seed(conn, "m1", 100, 10)
        conn.close()
        self.addCleanup(self.dir.cleanup)

    def test_views_are_overlaid_on_a_read_only_pre_v3_database(self):
        conn = connect(self.path, read_only=True)
        self.assertEqual(conn.execute(
            "SELECT attachment_state FROM mail_message_attachments").fetchone()[0],
            "unknown")
        self.assertFalse(attachments.coverage(conn).ever_ran)
        # Still genuinely read-only.
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("DELETE FROM mail_messages")

    def test_query_layer_reports_unknown_and_hides_the_column(self):
        from mailctx.query import MailContext
        with MailContext(self.path) as mc:
            row = mc.search("").rows[0]
        self.assertEqual(row["attachment_state"], "unknown")
        self.assertNotIn("has_attachments", row,
                         "the all-zero column must never reach the agent")


if __name__ == "__main__":
    unittest.main()
