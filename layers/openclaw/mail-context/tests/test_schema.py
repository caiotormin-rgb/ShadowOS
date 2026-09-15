import sqlite3
import unittest

from tests.fixtures import memory_conn
from pathlib import Path

from mailctx import SCHEMA_VERSION
from mailctx.store import connect, migrate

from mailctx.store import SCHEMA_PATH
SCHEMA_PATH_TEXT = SCHEMA_PATH.read_text()

# Words that would indicate someone added body/content storage to the index.
FORBIDDEN_COLUMN_TOKENS = ("body", "payload", "content", "raw", "html", "text_plain", "blob", "data")
ALLOWED_EXACT = {"mail_search"}  # FTS internals legitimately contain 'content='


class SchemaTest(unittest.TestCase):
    def setUp(self):
        self.conn = memory_conn()
        migrate(self.conn)

    def test_migrate_is_idempotent(self):
        migrate(self.conn)
        migrate(self.conn)
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

    def test_no_body_or_attachment_content_columns(self):
        """The no-bodies rule is enforced against the live schema, not just the docs."""
        offenders = []
        tables = [r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            if table in ALLOWED_EXACT or table.startswith("mail_search"):
                continue
            for col in self.conn.execute(f"PRAGMA table_info({table})"):
                name = col[1].lower()
                for tok in FORBIDDEN_COLUMN_TOKENS:
                    if tok in name:
                        offenders.append(f"{table}.{col[1]}")
        self.assertEqual(offenders, [], f"body/content-like columns found: {offenders}")

    def test_v1_database_migrates_in_place(self):
        """An index created before initial_complete existed must gain the column
        without losing its rows -- there are 5,000 real messages in one."""
        conn = memory_conn()
        conn.executescript(
            (SCHEMA_PATH_TEXT.replace(
                "  initial_complete    INTEGER NOT NULL DEFAULT 0\n", "")
             .replace(",\n  -- Whether a full initial load ever finished. Presence of a cursor is NOT a\n"
                      "  -- proxy for this: a bounded or failed load can leave a cursor behind, and\n"
                      "  -- inferring completion from it strands the unfetched remainder forever.",
                      "")))
        conn.execute("PRAGMA user_version = 1")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(mail_sync_state)")}
        self.assertNotIn("initial_complete", cols, "precondition: a v1 database")
        conn.execute("UPDATE mail_sync_state SET last_history_id = 999")

        migrate(conn)

        cols = {r[1] for r in conn.execute("PRAGMA table_info(mail_sync_state)")}
        self.assertIn("initial_complete", cols)
        row = conn.execute("SELECT last_history_id, initial_complete FROM mail_sync_state").fetchone()
        self.assertEqual(row[0], 999, "existing state survives the migration")
        self.assertEqual(row[1], 0, "an unfinished load must not be marked complete")
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

    def test_refuses_newer_schema(self):
        self.conn.execute("PRAGMA user_version = 99")
        with self.assertRaises(RuntimeError):
            migrate(self.conn)

    def test_draft_must_be_openclaw_owned(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO draft_links (draft_id, source_message_id, thread_id, purpose,
                       created_by_openclaw, source_message_id_at_creation, created_at, updated_at)
                   VALUES ('d1','m1','t1','reply',0,'m1',0,0)"""
            )

    def test_one_active_draft_per_source_and_purpose(self):
        ins = """INSERT INTO draft_links (draft_id, source_message_id, thread_id, purpose,
                     source_message_id_at_creation, state, created_at, updated_at)
                 VALUES (?,?,?,?,?,?,0,0)"""
        self.conn.execute(ins, ("d1", "m1", "t1", "reply", "m1", "active"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(ins, ("d2", "m1", "t1", "reply", "m1", "active"))
        # superseding the first one frees the slot
        self.conn.execute("UPDATE draft_links SET state='superseded' WHERE draft_id='d1'")
        self.conn.execute(ins, ("d3", "m1", "t1", "reply", "m1", "active"))
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM draft_links WHERE state='active'").fetchone()[0], 1)

    def test_candidate_category_is_constrained(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO action_candidates (source_message_id, source_thread_id, category,
                       created_at, updated_at) VALUES ('m1','t1','send_money',0,0)"""
            )


if __name__ == "__main__":
    unittest.main()
