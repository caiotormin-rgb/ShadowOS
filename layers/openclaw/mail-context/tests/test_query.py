import sqlite3
import tempfile
import unittest
from pathlib import Path

from mailctx.query import MailContext, _fts_query
from mailctx.store import Store, connect, migrate
from tests.fixtures import DAY, T0, msg


class QueryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "state" / "mail-context.sqlite"
        conn = connect(self.db)
        migrate(conn)
        self.store = Store(conn)
        self.store.upsert_message(msg("m1", thread="t1", ts=T0, subject="Dentist appointment",
                                      snippet="see you Tuesday", labels=("INBOX",)))
        self.store.upsert_message(msg("m2", thread="t1", ts=T0 + 60, subject="Re: Dentist appointment",
                                      from_addr="caio@example.invalid", labels=("SENT",)))
        self.store.upsert_message(msg("m3", thread="t2", ts=T0 + 120, subject="Electricity bill",
                                      from_addr="billing@utility.invalid",
                                      snippet="amount due 84.20", labels=("INBOX", "IMPORTANT")))
        self.store.advance_cursor(999, now=T0 + 200)
        conn.close()
        self.addCleanup(self.tmp.cleanup)

    def ctx(self, now=T0 + 300):
        c = MailContext(self.db, now=now)
        self.addCleanup(c.close)
        return c

    def test_created_database_is_private(self):
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.db.parent.stat().st_mode & 0o777, 0o700)

    def test_surface_is_physically_read_only(self):
        c = self.ctx()
        with self.assertRaises(sqlite3.OperationalError):
            c._conn.execute("DELETE FROM mail_messages")

    def test_search_matches_subject_and_snippet(self):
        self.assertEqual([r["message_id"] for r in self.ctx().search("dentist").rows],
                         ["m2", "m1"])
        self.assertEqual([r["message_id"] for r in self.ctx().search("84.20").rows], ["m3"])

    def test_search_filters_compose(self):
        res = self.ctx().search("dentist", sender="caio@")
        self.assertEqual([r["message_id"] for r in res.rows], ["m2"])
        res = self.ctx().search("", labels=["INBOX", "IMPORTANT"])
        self.assertEqual([r["message_id"] for r in res.rows], ["m3"],
                         "multiple labels must AND, not OR")

    def test_fts_operators_in_untrusted_text_are_neutralised(self):
        """Mail content is untrusted; it must not become FTS syntax."""
        for hostile in ['dentist OR bill', 'subject:"x"', 'NEAR(a b)', '"', 'a*']:
            with self.subTest(hostile=hostile):
                self.ctx().search(hostile)  # must not raise
        self.assertEqual(_fts_query("dentist OR bill"), '"dentist" "OR" "bill"')
        self.assertEqual([r["message_id"] for r in self.ctx().search("dentist OR bill").rows], [],
                         "OR must be treated as a literal token, not an operator")

    def test_tombstoned_messages_are_excluded(self):
        conn = connect(self.db)
        Store(conn).tombstone_message("m3", now=T0 + 200)
        conn.close()
        self.assertEqual([r["message_id"] for r in self.ctx().search("electricity").rows], [])

    def test_thread_returns_chronological_order(self):
        self.assertEqual([r["message_id"] for r in self.ctx().thread("t1").rows], ["m1", "m2"])

    def test_rows_carry_labels_and_attachment_metadata(self):
        row = self.ctx().thread("t2").rows[0]
        self.assertEqual(row["labels"], ["IMPORTANT", "INBOX"])
        self.assertEqual(row["attachments"], [])
        self.assertNotIn("body", row)

    def test_truncation_is_reported(self):
        res = self.ctx().search("", limit=1)
        self.assertEqual(len(res.rows), 1)
        self.assertTrue(res.truncated)

    def test_every_response_carries_freshness(self):
        for res in (self.ctx().search("dentist"), self.ctx().thread("t1"),
                    self.ctx().candidates(), self.ctx().status()):
            self.assertEqual(res.freshness.age_seconds, 100)
            self.assertFalse(res.freshness.is_stale)

    def test_stale_index_says_so(self):
        res = self.ctx(now=T0 + 200 + 40 * 3600).search("dentist")
        self.assertTrue(res.freshness.is_stale)
        self.assertIn("STALE", res.freshness.describe())

    def test_never_synced_index_is_stale_not_silent(self):
        empty = Path(self.tmp.name) / "empty" / "db.sqlite"
        conn = connect(empty); migrate(conn); conn.close()
        res = MailContext(empty, now=T0).status()
        self.addCleanup(lambda: None)
        self.assertTrue(res.freshness.is_stale)
        self.assertIn("never synchronized", res.freshness.describe())

    def test_status_counts(self):
        row = self.ctx().status().rows[0]
        self.assertEqual(row["messages"], 3)
        self.assertEqual(row["threads"], 2)
        self.assertEqual(row["active_drafts"], 0)

    def test_limit_is_capped(self):
        res = self.ctx().search("", limit=10_000)
        self.assertLessEqual(len(res.rows), 100)


if __name__ == "__main__":
    unittest.main()
