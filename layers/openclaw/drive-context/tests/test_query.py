import sqlite3
import tempfile
import unittest
from pathlib import Path

from drivectx.drive import FOLDER_MIME
from drivectx.query import DriveContext, _fts_query
from drivectx.sharing import Sharing
from drivectx.store import Store, connect, migrate
from tests.fixtures import DAY, T0, dfile


class QueryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "state" / "drive-context.sqlite"
        conn = connect(self.db)
        migrate(conn)
        store = Store(conn)
        store.upsert_file(dfile("d1", name="Taxes", mime=FOLDER_MIME, modified=T0 - 10))
        store.upsert_file(dfile("f1", name="Tax return 2025", parents=("d1",), modified=T0,
                                sharing=Sharing(state="private")))
        store.upsert_file(dfile("f2", name="Invoice draft", parents=("d1",), modified=T0 + 60,
                                mime="application/pdf",
                                owners=(("Someone Else", "other@example.invalid"),),
                                sharing=Sharing(state="anyone_with_link", named_user_count=2,
                                                link_discoverable=True, max_role="writer")))
        store.upsert_file(dfile("f3", name="Old notes", modified=T0 - 400 * DAY, trashed=True))
        store.advance_cursor("tok-1", now=T0 + 200)
        conn.close()
        self.waiver = Path(self.tmp.name) / "waiver.txt"

    def ctx(self, now=T0 + 300):
        c = DriveContext(self.db, now=now, waiver_path=self.waiver)
        self.addCleanup(c.close)
        return c

    # -- the surface itself ----------------------------------------------
    def test_created_database_is_private(self):
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.db.parent.stat().st_mode & 0o777, 0o700)

    def test_surface_is_physically_read_only(self):
        c = self.ctx()
        with self.assertRaises(sqlite3.OperationalError):
            c._conn.execute("DELETE FROM drive_files")

    def test_there_is_no_content_or_mutation_method(self):
        for forbidden in ("open", "download", "export", "content", "delete", "share",
                          "trash", "rename", "move", "create", "update"):
            self.assertFalse(hasattr(DriveContext, forbidden),
                             f"the contract must not expose {forbidden}()")

    # -- search ------------------------------------------------------------
    def test_search_matches_file_names(self):
        self.assertEqual([r["file_id"] for r in self.ctx().search("tax").rows], ["f1"])
        self.assertEqual([r["file_id"] for r in self.ctx().search("taxes").rows], ["d1"])

    def test_search_matches_whole_tokens_not_prefixes(self):
        """FTS5 without a prefix index matches tokens, so 'tax' does not find
        'Taxes'. Documented here so the behaviour is a decision, not a surprise."""
        self.assertEqual([r["file_id"] for r in self.ctx().search("invoice draft").rows], ["f2"])
        self.assertEqual([r["file_id"] for r in self.ctx().search("invoic").rows], [])

    def test_search_orders_by_most_recently_modified(self):
        self.assertEqual([r["file_id"] for r in self.ctx().search("").rows[:2]], ["f2", "f1"])

    def test_search_excludes_trashed_by_default_but_can_include_it(self):
        self.assertEqual([r["file_id"] for r in self.ctx().search("notes").rows], [])
        self.assertEqual(
            [r["file_id"] for r in self.ctx().search("notes", include_trashed=True).rows], ["f3"])

    def test_search_filters_by_mime_type_or_by_kind(self):
        self.assertEqual([r["file_id"] for r in self.ctx().search("", mime_type="folder").rows],
                         ["d1"])
        self.assertEqual(
            [r["file_id"] for r in self.ctx().search("", mime_type="application/pdf").rows], ["f2"])

    def test_search_filters_by_owner(self):
        self.assertEqual([r["file_id"] for r in self.ctx().search("", owner="other@").rows], ["f2"])
        self.assertEqual([r["file_id"] for r in self.ctx().search("", owner="Someone").rows], ["f2"])

    def test_search_filters_by_modified_since(self):
        self.assertEqual(
            [r["file_id"] for r in self.ctx().search("", modified_since=T0 + 30).rows], ["f2"])

    def test_tombstoned_files_are_excluded(self):
        conn = connect(self.db)
        Store(conn).tombstone_file("f2", now=T0 + 100)
        conn.close()
        self.assertEqual([r["file_id"] for r in self.ctx().search("invoice").rows], [])

    def test_fts_operators_in_untrusted_names_are_neutralised(self):
        """A file can be named `" OR 1=1` or `NEAR(x y)`. Names are data."""
        for hostile in ['tax OR invoice', 'name:"x"', 'NEAR(a b)', '"', 'a*', '" OR 1=1']:
            with self.subTest(hostile=hostile):
                self.ctx().search(hostile)  # must not raise
        self.assertEqual(_fts_query("tax OR invoice"), '"tax" "OR" "invoice"')
        self.assertEqual([r["file_id"] for r in self.ctx().search("tax OR invoice").rows], [],
                         "OR must be a literal token, not an operator")

    def test_truncation_is_reported(self):
        res = self.ctx().search("", limit=1)
        self.assertEqual(len(res.rows), 1)
        self.assertTrue(res.truncated)

    def test_limit_is_capped(self):
        self.assertLessEqual(len(self.ctx().search("", limit=10_000).rows), 100)

    # -- rows ---------------------------------------------------------------
    def test_rows_carry_parents_owners_and_a_sharing_summary(self):
        row = self.ctx().file("f2").rows[0]
        self.assertEqual(row["parents"], ["d1"])
        self.assertEqual(row["owners"], [{"display_name": "Someone Else",
                                          "email_address": "other@example.invalid"}])
        self.assertEqual(row["sharing"], {"state": "anyone_with_link", "named_user_count": 2,
                                          "group_count": 0, "link_discoverable": True,
                                          "max_role": "writer"})

    def test_rows_carry_no_content_and_no_grantee_list(self):
        row = self.ctx().file("f1").rows[0]
        for forbidden in ("content", "body", "thumbnail", "md5_checksum", "permissions"):
            self.assertNotIn(forbidden, row)

    def test_file_lookup_of_an_unknown_id_is_empty_not_an_error(self):
        self.assertEqual(self.ctx().file("nope").rows, [])

    def test_recent_returns_newest_first(self):
        self.assertEqual([r["file_id"] for r in self.ctx().recent(limit=2).rows], ["f2", "f1"])

    # -- freshness -----------------------------------------------------------
    def test_every_response_carries_freshness(self):
        c = self.ctx()
        for res in (c.search("tax"), c.file("f1"), c.path("f1"), c.recent(), c.status()):
            self.assertEqual(res.freshness.age_seconds, 100)
            self.assertFalse(res.freshness.is_stale)
            self.assertIn("last synced", res.freshness.describe())

    def test_freshness_has_the_shape_the_plan_specified(self):
        d = self.ctx().status().to_dict()["freshness"]
        self.assertEqual(set(d), {"last_success_at", "age_seconds", "status", "error_class",
                                  "is_stale", "description"})

    def test_a_stale_index_says_so(self):
        stale_by = DriveContext.STALE_AFTER + 3600
        res = self.ctx(now=T0 + 200 + stale_by).search("tax")
        self.assertTrue(res.freshness.is_stale)
        self.assertIn("STALE", res.freshness.describe())

    def test_a_never_synced_index_is_stale_not_silent(self):
        empty = Path(self.tmp.name) / "empty" / "db.sqlite"
        conn = connect(empty)
        migrate(conn)
        conn.close()
        with DriveContext(empty, now=T0, waiver_path=self.waiver) as c:
            res = c.status()
        self.assertTrue(res.freshness.is_stale)
        self.assertIn("never synchronized", res.freshness.describe())

    def test_a_failed_run_is_named_in_the_description(self):
        conn = connect(self.db)
        Store(conn).record_failure("auth")
        conn.close()
        self.assertIn("auth", self.ctx().status().freshness.describe())

    # -- status --------------------------------------------------------------
    def test_status_counts_and_exposure_summary(self):
        row = self.ctx().status().rows[0]
        self.assertEqual(row["files"], 4)
        self.assertEqual(row["folders"], 1)
        self.assertEqual(row["trashed"], 1)
        self.assertEqual(row["parent_edges"], 2)
        self.assertEqual(row["sharing"]["anyone_with_link"], 1)
        self.assertEqual(row["sharing"]["unknown"], 2)

    def test_status_reports_the_newest_run_even_when_two_share_a_second(self):
        """Ordering by timestamp alone let the CLI report a stale run: an
        initial and an incremental that start in the same second tie, and the
        tie broke arbitrarily."""
        conn = connect(self.db)
        store = Store(conn)
        first = store.start_run("initial", now=T0)
        store.finish_run(first, status="ok", now=T0)
        second = store.start_run("incremental", now=T0)
        store.finish_run(second, status="ok", added=7, now=T0)
        conn.close()
        last = self.ctx().status().rows[0]["last_run"]
        self.assertEqual(last["kind"], "incremental")
        self.assertEqual(last["added"], 7)

    def test_status_reports_whether_the_initial_listing_finished(self):
        self.assertFalse(self.ctx().status().rows[0]["initial_complete"])

    def test_status_surfaces_an_encryption_waiver_so_it_cannot_be_forgotten(self):
        self.assertIsNone(self.ctx().status().rows[0]["encryption_waiver"])
        self.waiver.write_text("torm root is plain ext4; accepted 2026-08-23\nmore detail\n")
        self.assertIn("plain ext4", self.ctx().status().rows[0]["encryption_waiver"])


if __name__ == "__main__":
    unittest.main()
