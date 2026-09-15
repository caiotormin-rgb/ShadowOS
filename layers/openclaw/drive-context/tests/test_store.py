import unittest

from drivectx.sharing import Sharing
from drivectx.store import TOMBSTONE_GRACE, Store, migrate
from tests.fixtures import DAY, T0, dfile, memory_conn


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.conn = memory_conn()
        migrate(self.conn)
        self.store = Store(self.conn)

    def live(self):
        return self.conn.execute(
            "SELECT COUNT(*) FROM drive_files WHERE deleted_at IS NULL").fetchone()[0]

    # -- upsert ----------------------------------------------------------
    def test_upsert_reports_added_then_updated(self):
        self.assertEqual(self.store.upsert_file(dfile("f1")), "added")
        self.assertEqual(self.store.upsert_file(dfile("f1")), "updated")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM drive_files").fetchone()[0], 1)

    def test_replaying_the_same_page_is_idempotent(self):
        """A replayed Changes page must not duplicate or corrupt rows."""
        batch = [dfile("f1", parents=("p1",)), dfile("f2", parents=("p1", "p2"))]
        for _ in range(3):
            for f in batch:
                self.store.upsert_file(f)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM drive_files").fetchone()[0], 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM drive_parents").fetchone()[0], 3)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM drive_owners").fetchone()[0], 2)

    def test_kind_is_derived_from_mime_type(self):
        from drivectx.drive import FOLDER_MIME, SHORTCUT_MIME
        self.store.upsert_file(dfile("d", mime=FOLDER_MIME))
        self.store.upsert_file(dfile("s", mime=SHORTCUT_MIME))
        self.store.upsert_file(dfile("f", mime="application/pdf"))
        kinds = dict(self.conn.execute("SELECT file_id, kind FROM drive_files"))
        self.assertEqual(kinds, {"d": "folder", "s": "shortcut", "f": "file"})

    def test_parent_edges_are_applied_not_accumulated(self):
        """A file moved out of a folder must lose that edge."""
        self.store.upsert_file(dfile("f1", parents=("old",)))
        self.store.upsert_file(dfile("f1", parents=("new",)))
        edges = [r[0] for r in self.conn.execute(
            "SELECT parent_id FROM drive_parents WHERE file_id='f1'")]
        self.assertEqual(edges, ["new"])

    def test_multiple_parents_are_all_kept(self):
        self.store.upsert_file(dfile("f1", parents=("a", "b", "c")))
        edges = sorted(r[0] for r in self.conn.execute(
            "SELECT parent_id FROM drive_parents WHERE file_id='f1'"))
        self.assertEqual(edges, ["a", "b", "c"])

    def test_owners_are_applied_not_accumulated(self):
        self.store.upsert_file(dfile("f1", owners=(("A", "a@x.invalid"),)))
        self.store.upsert_file(dfile("f1", owners=(("B", "b@x.invalid"),)))
        rows = [r[0] for r in self.conn.execute(
            "SELECT email_address FROM drive_owners WHERE file_id='f1'")]
        self.assertEqual(rows, ["b@x.invalid"])

    def test_duplicate_owner_entries_collapse(self):
        self.store.upsert_file(dfile("f1", owners=(("A", "a@x.invalid"), ("A", "a@x.invalid"))))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM drive_owners WHERE file_id='f1'").fetchone()[0], 1)

    def test_sharing_summary_is_persisted(self):
        self.store.upsert_file(dfile("f1", sharing=Sharing(
            state="anyone_with_link", named_user_count=3, group_count=1,
            link_discoverable=True, max_role="writer")))
        row = self.conn.execute("SELECT * FROM drive_files WHERE file_id='f1'").fetchone()
        self.assertEqual(row["sharing_state"], "anyone_with_link")
        self.assertEqual(row["named_user_count"], 3)
        self.assertEqual(row["group_count"], 1)
        self.assertEqual(row["link_discoverable"], 1)
        self.assertEqual(row["max_role"], "writer")

    def test_created_time_survives_a_payload_that_omits_it(self):
        """Drive's Changes payload can be thinner than the listing payload."""
        self.store.upsert_file(dfile("f1", modified=T0))
        self.store.upsert_file(dfile("f1", modified=T0 + 10, created_ts=None))
        row = self.conn.execute("SELECT * FROM drive_files WHERE file_id='f1'").fetchone()
        self.assertEqual(row["created_ts"], T0)
        self.assertEqual(row["modified_ts"], T0 + 10)

    # -- tombstones and trash --------------------------------------------
    def test_trashing_is_a_flag_not_a_deletion(self):
        self.store.upsert_file(dfile("f1", trashed=True))
        row = self.conn.execute("SELECT * FROM drive_files WHERE file_id='f1'").fetchone()
        self.assertEqual(row["trashed"], 1)
        self.assertIsNone(row["deleted_at"], "a trashed file is still indexed")
        self.assertEqual(self.live(), 1)

    def test_tombstone_hides_the_row_and_is_idempotent(self):
        self.store.upsert_file(dfile("f1"))
        self.assertTrue(self.store.tombstone_file("f1", now=T0))
        self.assertFalse(self.store.tombstone_file("f1", now=T0), "second tombstone is a no-op")
        self.assertEqual(self.live(), 0)

    def test_restored_file_clears_its_tombstone(self):
        """Drive can restore a file; the index must follow."""
        self.store.upsert_file(dfile("f1"))
        self.store.tombstone_file("f1", now=T0)
        self.store.upsert_file(dfile("f1"))
        self.assertIsNone(self.conn.execute(
            "SELECT deleted_at FROM drive_files WHERE file_id='f1'").fetchone()[0])

    def test_prune_expires_tombstones_only_after_the_grace_period(self):
        self.store.upsert_file(dfile("recent"))
        self.store.upsert_file(dfile("gone"))
        self.store.upsert_file(dfile("just_gone"))
        self.store.tombstone_file("gone", now=T0 - TOMBSTONE_GRACE - DAY)
        self.store.tombstone_file("just_gone", now=T0 - 60)
        pruned = self.store.prune(now=T0)
        self.assertEqual(pruned, 1)
        remaining = sorted(r[0] for r in self.conn.execute("SELECT file_id FROM drive_files"))
        self.assertEqual(remaining, ["just_gone", "recent"])

    def test_prune_never_touches_a_live_row(self):
        """Unlike mail-context there is no age window: a file from 2014 is
        exactly the one whose location the operator has forgotten."""
        self.store.upsert_file(dfile("ancient", modified=1))
        self.assertEqual(self.store.prune(now=T0), 0)
        self.assertEqual(self.live(), 1)

    # -- reconciliation ---------------------------------------------------
    def test_sweep_tombstones_rows_a_full_listing_did_not_touch(self):
        first = self.store.start_run("initial", now=T0 - DAY)
        self.store.upsert_file(dfile("stale"), now=T0 - DAY, run_id=first)
        self.store.upsert_file(dfile("fresh"), now=T0 - DAY, run_id=first)
        second = self.store.start_run("resync", now=T0)
        self.store.upsert_file(dfile("fresh"), now=T0, run_id=second)
        swept = self.store.sweep_missing(run_id=second, now=T0 + 20)
        self.assertEqual(swept, 1)
        self.assertEqual([r[0] for r in self.conn.execute(
            "SELECT file_id FROM drive_files WHERE deleted_at IS NULL")], ["fresh"])

    def test_the_sweep_marker_is_the_run_not_the_clock(self):
        """Two full listings inside one second share a timestamp. Sweeping on
        synced_at made the second one silently reconcile nothing."""
        first = self.store.start_run("initial", now=T0)
        self.store.upsert_file(dfile("stale"), now=T0, run_id=first)
        self.store.upsert_file(dfile("keep"), now=T0, run_id=first)
        second = self.store.start_run("resync", now=T0)
        self.store.upsert_file(dfile("keep"), now=T0, run_id=second)
        self.assertEqual(self.store.sweep_missing(run_id=second, now=T0), 1)
        self.assertEqual([r[0] for r in self.conn.execute(
            "SELECT file_id FROM drive_files WHERE deleted_at IS NULL")], ["keep"])

    def test_a_row_never_stamped_by_any_run_is_swept(self):
        self.store.upsert_file(dfile("legacy"))
        run = self.store.start_run("resync", now=T0)
        self.assertEqual(self.store.sweep_missing(run_id=run, now=T0), 1)

    def test_sweep_does_not_re_tombstone(self):
        first = self.store.start_run("initial", now=T0 - DAY)
        self.store.upsert_file(dfile("gone"), now=T0 - DAY, run_id=first)
        self.store.tombstone_file("gone", now=T0 - 100)
        second = self.store.start_run("resync", now=T0)
        self.assertEqual(self.store.sweep_missing(run_id=second, now=T0), 0)

    # -- resume ------------------------------------------------------------
    def test_existing_ids_reports_only_live_rows(self):
        self.store.upsert_file(dfile("a"))
        self.store.upsert_file(dfile("b"))
        self.store.tombstone_file("b", now=T0)
        self.assertEqual(self.store.existing_ids(["a", "b", "c"]), {"a"})

    def test_existing_ids_chunks_past_the_sqlite_variable_limit(self):
        ids = [f"f{i}" for i in range(2500)]
        for i in ids:
            self.store.upsert_file(dfile(i))
        self.assertEqual(len(self.store.existing_ids(ids)), 2500)

    def test_existing_ids_of_nothing_is_empty(self):
        self.assertEqual(self.store.existing_ids([]), set())

    # -- cursor ------------------------------------------------------------
    def test_cursor_advances_only_as_told(self):
        self.assertIsNone(self.store.cursor())
        self.store.advance_cursor("tok-1", now=T0)
        state = self.conn.execute("SELECT * FROM drive_sync_state").fetchone()
        self.assertEqual(state["page_token"], "tok-1")
        self.assertEqual(state["status"], "ok")
        self.assertIsNone(state["last_full_sync_at"], "incremental sync is not a full sync")
        self.store.advance_cursor("tok-2", kind="resync", now=T0 + 5)
        self.assertEqual(self.conn.execute(
            "SELECT last_full_sync_at FROM drive_sync_state").fetchone()[0], T0 + 5)

    def test_clearing_the_cursor_leaves_the_index_alone(self):
        """An expired page token makes the index stale, not wrong."""
        self.store.upsert_file(dfile("f1"))
        self.store.advance_cursor("tok-1", now=T0)
        self.store.clear_cursor()
        self.assertIsNone(self.store.cursor())
        self.assertEqual(self.live(), 1)

    def test_failure_is_recorded_without_losing_the_last_good_cursor(self):
        self.store.advance_cursor("tok-1", now=T0)
        self.store.record_failure("auth")
        state = self.conn.execute("SELECT * FROM drive_sync_state").fetchone()
        self.assertEqual((state["status"], state["error_class"]), ("error", "auth"))
        self.assertEqual(state["page_token"], "tok-1",
                         "a failed run must preserve the last good cursor")

    def test_root_folder_id_roundtrips(self):
        self.assertIsNone(self.store.root_folder_id())
        self.store.set_root_folder_id("root-abc")
        self.assertEqual(self.store.root_folder_id(), "root-abc")

    # -- transactions ------------------------------------------------------
    def test_rollback_leaves_no_partial_page(self):
        self.store.begin()
        self.store.upsert_file(dfile("f1"))
        self.store.rollback()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM drive_files").fetchone()[0], 0)

    def test_abort_is_safe_whether_or_not_a_transaction_is_open(self):
        """Error handlers need their own transaction and may be reached with one
        already open. BEGIN inside a transaction is an error, so abort() checks."""
        self.assertFalse(self.store.abort())
        self.store.begin()
        self.assertTrue(self.store.abort())
        self.store.begin()  # must not raise "cannot start a transaction within a transaction"
        self.store.commit()

    def test_run_bookkeeping_records_counts_and_error_class(self):
        run_id = self.store.start_run("initial", now=T0)
        self.store.finish_run(run_id, status="error", added=2, updated=1, deleted=3,
                              pruned=4, excluded=5, error_class="network", now=T0 + 9)
        row = self.conn.execute("SELECT * FROM drive_sync_runs").fetchone()
        self.assertEqual(
            (row["kind"], row["status"], row["added"], row["updated"], row["deleted"],
             row["pruned"], row["excluded"], row["error_class"], row["finished_at"]),
            ("initial", "error", 2, 1, 3, 4, 5, "network", T0 + 9))


if __name__ == "__main__":
    unittest.main()
