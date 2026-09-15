import sqlite3
import unittest

from mailctx.store import Message, Store, migrate
from tests.fixtures import memory_conn, DAY, T0, msg


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.conn = memory_conn()
        migrate(self.conn)
        self.store = Store(self.conn)

    def test_upsert_reports_added_then_updated(self):
        self.assertEqual(self.store.upsert_message(msg("m1")), "added")
        self.assertEqual(self.store.upsert_message(msg("m1")), "updated")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM mail_messages").fetchone()[0], 1)

    def test_replaying_the_same_page_is_idempotent(self):
        """A replayed Gmail history page must not duplicate or corrupt rows."""
        batch = [msg("m1", thread="t1"), msg("m2", thread="t1", ts=T0 + 10)]
        for _ in range(3):
            for m in batch:
                self.store.upsert_message(m)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM mail_messages").fetchone()[0], 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM mail_labels").fetchone()[0], 2)
        thread = self.conn.execute("SELECT * FROM mail_threads WHERE thread_id='t1'").fetchone()
        self.assertEqual(thread["message_count"], 2)
        self.assertEqual(thread["last_message_ts"], T0 + 10)

    def test_label_changes_are_applied_not_accumulated(self):
        self.store.upsert_message(msg("m1", labels=("INBOX", "UNREAD")))
        self.store.upsert_message(msg("m1", labels=("INBOX",)))
        labels = [r[0] for r in self.conn.execute(
            "SELECT label FROM mail_labels WHERE message_id='m1'")]
        self.assertEqual(labels, ["INBOX"])

    def test_attachments_store_metadata_only(self):
        self.store.upsert_message(
            msg("m1", attachments=(("invoice.pdf", "application/pdf", 12345),)))
        row = self.conn.execute("SELECT * FROM mail_attachments").fetchone()
        self.assertEqual(row["filename"], "invoice.pdf")
        self.assertEqual(row["mime_type"], "application/pdf")
        # The stored MIME part is the only record of presence. There is
        # deliberately no has_attachments boolean beside it (schema v3): that
        # column was fed by a MIME walk that never fires under format=metadata,
        # so it read 0 for every one of 132,588 real messages while looking
        # exactly like an authoritative "no".
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(mail_messages)")}
        self.assertNotIn("has_attachments", cols)
        state = self.conn.execute(
            "SELECT attachment_state FROM mail_message_attachments "
            "WHERE message_id='m1'").fetchone()[0]
        self.assertEqual(state, "yes")

    def test_tombstone_hides_message_and_updates_thread(self):
        self.store.upsert_message(msg("m1", thread="t1"))
        self.store.upsert_message(msg("m2", thread="t1", ts=T0 + 10))
        self.assertTrue(self.store.tombstone_message("m2", now=T0 + 100))
        self.assertFalse(self.store.tombstone_message("m2", now=T0 + 100), "second tombstone is a no-op")
        thread = self.conn.execute("SELECT * FROM mail_threads WHERE thread_id='t1'").fetchone()
        self.assertEqual(thread["message_count"], 1)
        self.assertEqual(thread["last_message_ts"], T0)

    def test_resurrected_message_clears_its_tombstone(self):
        """Gmail can un-trash a message; the index must follow."""
        self.store.upsert_message(msg("m1"))
        self.store.tombstone_message("m1", now=T0)
        self.store.upsert_message(msg("m1"))
        self.assertIsNone(
            self.conn.execute("SELECT deleted_at FROM mail_messages WHERE message_id='m1'").fetchone()[0])

    def test_prune_enforces_rolling_window_and_expires_tombstones(self):
        self.store.upsert_message(msg("old", ts=T0 - 400 * DAY))
        self.store.upsert_message(msg("recent", ts=T0))
        self.store.upsert_message(msg("gone", ts=T0))
        self.store.tombstone_message("gone", now=T0 - 30 * DAY)
        pruned = self.store.prune(window_start_ts=T0 - 365 * DAY, now=T0)
        self.assertEqual(pruned, 2)
        remaining = [r[0] for r in self.conn.execute("SELECT message_id FROM mail_messages")]
        self.assertEqual(remaining, ["recent"])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM mail_threads").fetchone()[0], 1,
            "threads with no surviving messages are dropped")

    def test_history_id_never_moves_backwards(self):
        self.store.upsert_message(msg("m1", history_id=500))
        self.store.upsert_message(msg("m1", history_id=200))
        self.assertEqual(
            self.conn.execute("SELECT history_id FROM mail_messages WHERE message_id='m1'").fetchone()[0],
            500)

    def test_cursor_advances_only_as_told(self):
        self.store.advance_cursor(1000, now=T0)
        state = self.conn.execute("SELECT * FROM mail_sync_state").fetchone()
        self.assertEqual(state["last_history_id"], 1000)
        self.assertEqual(state["status"], "ok")
        self.assertIsNone(state["last_full_sync_at"], "incremental sync is not a full sync")
        self.store.advance_cursor(2000, kind="resync", now=T0 + 5)
        state = self.conn.execute("SELECT * FROM mail_sync_state").fetchone()
        self.assertEqual(state["last_full_sync_at"], T0 + 5)

    def test_failure_is_recorded_without_losing_last_good_cursor(self):
        self.store.advance_cursor(1000, now=T0)
        self.store.record_failure("auth")
        state = self.conn.execute("SELECT * FROM mail_sync_state").fetchone()
        self.assertEqual(state["status"], "error")
        self.assertEqual(state["error_class"], "auth")
        self.assertEqual(state["last_history_id"], 1000, "a failed run must preserve the last good index")

    def test_rollback_leaves_no_partial_page(self):
        self.store.begin()
        self.store.upsert_message(msg("m1"))
        self.store.rollback()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM mail_messages").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
