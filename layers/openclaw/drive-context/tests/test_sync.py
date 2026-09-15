import unittest

from drivectx.drive import TransportError
from drivectx.store import Store, migrate
from drivectx.sync import MAX_RESYNC_PAGES, Syncer, parse_file
from tests.fixtures import (T0, change, memory_conn, perm, raw_file, raw_folder,
                            raw_shortcut, rfc3339)


class FakeApi:
    """Stands in for Drive. Records calls so ordering can be asserted.

    Deliberately paginates the way Drive does, because the ordering bugs this
    layer can have -- cursor before listing, cursor after commit -- are only
    visible across page boundaries.
    """

    def __init__(self, files=None, change_pages=None, start_token="tok-0",
                 root="root-id", page_size=1000):
        self.files = {f["id"]: f for f in (files or [])}
        self.change_pages = list(change_pages or [])
        self.start_token = start_token
        self.root = root
        self.page_size = page_size
        self.calls = []
        self.include_shared_drives = False
        self.fail_changes_with = None
        self.fail_list_with = None
        self.list_pages_served = 0

    def start_page_token(self):
        self.calls.append("start_page_token")
        return self.start_token

    def root_folder_id(self):
        self.calls.append("root")
        return self.root

    def list_files(self, *, page_token=None, page_size=1000, query=None):
        self.calls.append(f"list:{page_token}")
        if self.fail_list_with:
            raise TransportError(self.fail_list_with, 500)
        ids = list(self.files)
        size = min(self.page_size, page_size)
        start = int(page_token or 0)
        page = ids[start:start + size]
        self.list_pages_served += 1
        out = {"files": [self.files[i] for i in page]}
        if start + size < len(ids):
            out["nextPageToken"] = str(start + size)
        return out

    def get_file(self, file_id):
        self.calls.append(f"get:{file_id}")
        if file_id not in self.files:
            raise TransportError("not_found", 404)
        return self.files[file_id]

    def list_changes(self, page_token, *, page_size=1000):
        self.calls.append(f"changes:{page_token}")
        if self.fail_changes_with:
            raise TransportError(self.fail_changes_with, 410)
        if self.change_pages:
            return self.change_pages.pop(0)
        return {"changes": [], "newStartPageToken": "tok-next"}


class SyncTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = memory_conn()
        migrate(self.conn)
        self.store = Store(self.conn)

    def live(self):
        return self.conn.execute(
            "SELECT COUNT(*) FROM drive_files WHERE deleted_at IS NULL").fetchone()[0]

    def name_of(self, file_id):
        row = self.conn.execute(
            "SELECT name FROM drive_files WHERE file_id = ?", (file_id,)).fetchone()
        return row[0] if row else None


class ParseTest(SyncTestCase):
    def test_parse_maps_every_retained_field(self):
        f = parse_file(raw_file("f1", name="Taxes", mime="application/pdf",
                                parents=("p1", "p2"), created=T0, modified=T0 + 60,
                                size=4096, starred=True))
        self.assertEqual(f.file_id, "f1")
        self.assertEqual(f.name, "Taxes")
        self.assertEqual(f.parents, ("p1", "p2"))
        self.assertEqual((f.created_ts, f.modified_ts), (T0, T0 + 60))
        self.assertEqual(f.size_bytes, 4096)
        self.assertTrue(f.starred)
        self.assertEqual(f.kind, "file")

    def test_google_native_documents_report_no_size(self):
        """NULL means 'Drive did not say', never zero."""
        f = parse_file(raw_file("f1", mime="application/vnd.google-apps.document", size=None))
        self.assertIsNone(f.size_bytes)

    def test_folders_and_shortcuts_are_classified(self):
        self.assertEqual(parse_file(raw_folder("d1")).kind, "folder")
        s = parse_file(raw_shortcut("s1", target="t1"))
        self.assertEqual(s.kind, "shortcut")
        self.assertEqual(s.shortcut_target_id, "t1")

    def test_timestamps_are_read_as_utc(self):
        raw = raw_file("f1")
        raw["modifiedTime"] = rfc3339(T0)
        self.assertEqual(parse_file(raw).modified_ts, T0)

    def test_a_payload_without_an_id_is_dropped_not_crashed(self):
        self.assertIsNone(parse_file({"name": "orphaned payload"}))

    def test_a_nonsense_size_does_not_crash_the_parse(self):
        raw = raw_file("f1")
        raw["size"] = "not-a-number"
        self.assertIsNone(parse_file(raw).size_bytes)

    def test_a_file_with_no_parents_parses_as_parentless(self):
        self.assertEqual(parse_file(raw_file("f1")).parents, ())


class InitialSyncTest(SyncTestCase):
    def test_cursor_is_captured_before_the_listing(self):
        """Otherwise every change during the listing is silently lost."""
        api = FakeApi([raw_file("f1"), raw_file("f2")], start_token="tok-A")
        Syncer(self.store, api).initial_sync()
        self.assertEqual(api.calls[0], "start_page_token", "cursor must be captured first")
        self.assertLess(api.calls.index("start_page_token"), api.calls.index("list:None"))
        self.assertEqual(self.store.cursor(), "tok-A")

    def test_cursor_is_stored_only_after_the_data_commits(self):
        """Storing it first would drop every file the failing page carried."""
        api = FakeApi([raw_file("f1")])
        api.fail_list_with = "network"
        res = Syncer(self.store, api).initial_sync()
        self.assertEqual((res.status, res.error_class), ("error", "network"))
        self.assertIsNone(self.store.cursor(), "a failed listing must not leave a cursor")

    def test_initial_sync_indexes_everything_with_edges_and_owners(self):
        api = FakeApi([raw_folder("d1", name="Docs"),
                       raw_file("f1", parents=("d1",)),
                       raw_file("f2", parents=("d1",))])
        res = Syncer(self.store, api).initial_sync()
        self.assertEqual((res.added, res.updated), (3, 0))
        self.assertEqual(self.live(), 3)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM drive_parents WHERE parent_id='d1'").fetchone()[0], 2)

    def test_initial_sync_records_the_root_folder_id(self):
        api = FakeApi([raw_file("f1")], root="root-xyz")
        Syncer(self.store, api).initial_sync()
        self.assertEqual(self.store.root_folder_id(), "root-xyz")

    def test_initial_sync_is_idempotent(self):
        api = FakeApi([raw_file("f1"), raw_file("f2")])
        s = Syncer(self.store, api)
        s.initial_sync()
        res = s.initial_sync()
        self.assertEqual((res.added, res.updated), (0, 0), "everything is already stored")
        self.assertEqual(self.live(), 2)

    def test_rerunning_an_interrupted_listing_skips_what_is_already_stored(self):
        api = FakeApi([raw_file(f"f{i}") for i in range(30)], page_size=10)
        s = Syncer(self.store, api)
        s.initial_sync(max_files=10)
        self.assertEqual(self.live(), 10)
        s2 = Syncer(self.store, api)
        s2.initial_sync()
        self.assertEqual(self.live(), 30)
        self.assertEqual(s2.skipped, 10, "already-stored rows must not be rewritten")

    def test_a_bounded_run_does_not_claim_the_listing_is_complete(self):
        """The cursor is captured before the listing, so its presence proves
        nothing about completeness. Dispatching on it would strand the rest."""
        api = FakeApi([raw_file(f"f{i}") for i in range(30)], page_size=10)
        res = Syncer(self.store, api).initial_sync(max_files=10)
        self.assertFalse(res.complete)
        self.assertIsNotNone(self.store.cursor())
        self.assertFalse(self.store.initial_complete())

    def test_sync_keeps_listing_until_the_listing_finishes(self):
        api = FakeApi([raw_file(f"f{i}") for i in range(30)], page_size=10)
        s = Syncer(self.store, api)
        s.initial_sync(max_files=10)
        self.assertEqual(s.sync().kind, "initial", "a partial index must keep listing")
        self.assertTrue(self.store.initial_complete())
        self.assertEqual(s.sync().kind, "incremental")

    def test_shared_drive_items_are_excluded_by_default(self):
        api = FakeApi([raw_file("mine"), raw_file("theirs", drive_id="shared-drive-1")])
        s = Syncer(self.store, api)
        self.assertFalse(s.include_shared_drives, "the default must be false")
        res = s.initial_sync()
        self.assertEqual(res.excluded, 1)
        self.assertEqual([r[0] for r in self.conn.execute("SELECT file_id FROM drive_files")],
                         ["mine"])

    def test_shared_drive_items_are_indexed_only_when_the_setting_is_on(self):
        api = FakeApi([raw_file("mine"), raw_file("theirs", drive_id="shared-drive-1")])
        res = Syncer(self.store, api, include_shared_drives=True).initial_sync()
        self.assertEqual(res.excluded, 0)
        self.assertEqual(self.live(), 2)

    def test_the_setting_is_pushed_down_to_the_transport(self):
        api = FakeApi()
        Syncer(self.store, api, include_shared_drives=True)
        self.assertTrue(api.include_shared_drives)

    def test_transport_failure_records_a_class_and_preserves_the_index(self):
        api = FakeApi([raw_file("f1")])
        Syncer(self.store, api).initial_sync()
        api.fail_list_with = "auth"
        res = Syncer(self.store, api).initial_sync()
        self.assertEqual((res.status, res.error_class), ("error", "auth"))
        self.assertEqual(self.live(), 1, "a failed run must not destroy the last good index")
        state = self.conn.execute("SELECT * FROM drive_sync_state").fetchone()
        self.assertEqual(state["status"], "error")

    def test_a_failed_run_can_record_its_failure_without_a_transaction_crash(self):
        """abort() before begin(): an error handler reached with a transaction
        already open must roll back, not raise 'cannot start a transaction
        within a transaction'."""
        api = FakeApi([raw_file("f1")])
        api.fail_list_with = "network"
        res = Syncer(self.store, api).initial_sync()
        self.assertEqual(res.error_class, "network")
        self.assertFalse(self.conn.in_transaction)


class ResyncTest(SyncTestCase):
    def test_an_expired_page_token_triggers_a_bounded_resync(self):
        api = FakeApi([raw_file("f1")])
        s = Syncer(self.store, api)
        s.initial_sync()
        api.fail_changes_with = "stale_page_token"
        res = s.incremental_sync()
        self.assertTrue(res.resynced)
        self.assertEqual(res.kind, "resync")
        self.assertEqual(res.status, "ok")

    def test_the_resync_is_recorded_as_its_own_kind(self):
        api = FakeApi([raw_file("f1")])
        s = Syncer(self.store, api)
        s.initial_sync()
        api.fail_changes_with = "stale_page_token"
        s.incremental_sync()
        kinds = [r[0] for r in self.conn.execute(
            "SELECT kind FROM drive_sync_runs ORDER BY run_id")]
        self.assertIn("resync", kinds)

    def test_at_most_one_resync_per_run(self):
        """A resync that itself fails must not recurse into another one."""
        api = FakeApi([raw_file("f1")])
        s = Syncer(self.store, api)
        s.initial_sync()
        api.fail_changes_with = "stale_page_token"
        s.incremental_sync()
        resyncs = [r[0] for r in self.conn.execute(
            "SELECT kind FROM drive_sync_runs WHERE kind='resync'")]
        self.assertEqual(len(resyncs), 1)

    def test_a_completed_resync_tombstones_files_that_vanished(self):
        """Changes told us nothing about them, so only a full listing can."""
        api = FakeApi([raw_file("keep"), raw_file("vanish")])
        s = Syncer(self.store, api)
        s.initial_sync()
        self.assertEqual(self.live(), 2)
        del api.files["vanish"]
        api.fail_changes_with = "stale_page_token"
        res = s.incremental_sync()
        self.assertEqual(res.deleted, 1)
        self.assertEqual([r[0] for r in self.conn.execute(
            "SELECT file_id FROM drive_files WHERE deleted_at IS NULL")], ["keep"])

    def test_a_truncated_resync_never_sweeps(self):
        """Anything an incomplete listing did not touch is unproven, not gone.
        Sweeping on unproven absence would delete the index one bad run at a
        time."""
        api = FakeApi([raw_file(f"f{i}") for i in range(30)], page_size=10)
        s = Syncer(self.store, api, max_resync_pages=2)
        s.initial_sync()
        self.assertEqual(self.live(), 30)
        res = s.initial_sync(kind="resync")
        self.assertEqual(res.error_class, "resync_budget_exceeded")
        self.assertEqual(self.live(), 30, "the previous good index survives")

    def test_a_resync_that_outruns_its_budget_fails_and_keeps_the_old_cursor(self):
        api = FakeApi([raw_file(f"f{i}") for i in range(30)], page_size=10)
        s = Syncer(self.store, api, max_resync_pages=2)
        s.initial_sync()
        before = self.store.cursor()
        api.start_token = "tok-NEW"
        res = s.initial_sync(kind="resync")
        self.assertEqual((res.status, res.error_class), ("error", "resync_budget_exceeded"))
        self.assertEqual(self.store.cursor(), before, "a failed resync must not advance")

    def test_the_resync_page_budget_is_a_real_constant(self):
        self.assertGreater(MAX_RESYNC_PAGES, 0)
        api = FakeApi()
        self.assertEqual(Syncer(self.store, api).max_resync_pages, MAX_RESYNC_PAGES)

    def test_a_resync_never_uses_the_resume_skip(self):
        """A reconciliation pass must touch every row, or the sweep would
        tombstone the rows it skipped."""
        api = FakeApi([raw_file("f1"), raw_file("f2")])
        s = Syncer(self.store, api)
        s.initial_sync()
        s.skipped = 0
        res = s.initial_sync(kind="resync")
        self.assertEqual(s.skipped, 0)
        self.assertEqual(res.updated, 2)
        self.assertEqual(self.live(), 2, "the sweep must not eat rows it just refreshed")


class IncrementalSyncTest(SyncTestCase):
    def bootstrap(self, files):
        api = FakeApi(files)
        s = Syncer(self.store, api)
        s.initial_sync()
        return api, s

    def test_incremental_applies_additions_and_removals(self):
        api, s = self.bootstrap([raw_file("f1")])
        api.change_pages = [{
            "changes": [change("f2", file=raw_file("f2")), change("f1", removed=True)],
            "newStartPageToken": "tok-1"}]
        res = s.incremental_sync()
        self.assertEqual((res.added, res.deleted), (1, 1))
        self.assertEqual(self.live(), 1)
        self.assertEqual(self.store.cursor(), "tok-1")

    def test_incremental_refetches_a_changed_file_instead_of_skipping_it(self):
        """The resume optimisation must never apply to an incremental run: the
        stored row is exactly the stale one."""
        api, s = self.bootstrap([raw_file("f1", name="Old name")])
        self.assertEqual(self.name_of("f1"), "Old name")
        s.skipped = 0
        api.change_pages = [{"changes": [change("f1", file=raw_file("f1", name="New name"))],
                             "newStartPageToken": "tok-1"}]
        res = s.incremental_sync()
        self.assertEqual(self.name_of("f1"), "New name", "a changed file must be reapplied")
        self.assertEqual(s.skipped, 0, "incremental sync must not skip stored rows")
        self.assertEqual(res.updated, 1)

    def test_a_move_replaces_the_parent_edge(self):
        api, s = self.bootstrap([raw_file("f1", parents=("old",))])
        api.change_pages = [{"changes": [change("f1", file=raw_file("f1", parents=("new",)))],
                             "newStartPageToken": "tok-1"}]
        s.incremental_sync()
        self.assertEqual([r[0] for r in self.conn.execute(
            "SELECT parent_id FROM drive_parents WHERE file_id='f1'")], ["new"])

    def test_trashing_sets_the_flag_and_keeps_the_row_live(self):
        api, s = self.bootstrap([raw_file("f1")])
        api.change_pages = [{"changes": [change("f1", file=raw_file("f1", trashed=True))],
                             "newStartPageToken": "tok-1"}]
        s.incremental_sync()
        row = self.conn.execute("SELECT * FROM drive_files WHERE file_id='f1'").fetchone()
        self.assertEqual(row["trashed"], 1)
        self.assertIsNone(row["deleted_at"], "trashing is a flag, not a deletion")

    def test_a_change_with_no_file_payload_is_treated_as_a_removal(self):
        """Losing access looks exactly like a deletion from here, and the index
        records what this account can see."""
        api, s = self.bootstrap([raw_file("f1")])
        api.change_pages = [{"changes": [change("f1")], "newStartPageToken": "tok-1"}]
        res = s.incremental_sync()
        self.assertEqual(res.deleted, 1)

    def test_a_file_that_moved_into_a_shared_drive_leaves_the_index(self):
        api, s = self.bootstrap([raw_file("f1")])
        api.change_pages = [{
            "changes": [change("f1", file=raw_file("f1", drive_id="shared-1"))],
            "newStartPageToken": "tok-1"}]
        res = s.incremental_sync()
        self.assertEqual(res.excluded, 1)
        self.assertEqual(self.live(), 0, "a stale row would go on lying forever")

    def test_a_shared_drive_change_record_is_ignored(self):
        api, s = self.bootstrap([raw_file("f1")])
        api.change_pages = [{"changes": [change("drive-1", change_type="drive")],
                             "newStartPageToken": "tok-1"}]
        res = s.incremental_sync()
        self.assertEqual((res.excluded, res.deleted), (1, 0))
        self.assertEqual(self.live(), 1)

    def test_the_cursor_advances_page_by_page(self):
        api, s = self.bootstrap([raw_file("f1")])
        api.change_pages = [
            {"changes": [change("f2", file=raw_file("f2"))], "nextPageToken": "tok-p2"},
            {"changes": [change("f3", file=raw_file("f3"))], "newStartPageToken": "tok-final"},
        ]
        res = s.incremental_sync()
        self.assertEqual(res.pages, 2)
        self.assertEqual(self.store.cursor(), "tok-final")
        self.assertEqual(api.calls[-2:], ["changes:tok-0", "changes:tok-p2"])

    def test_a_failure_mid_walk_leaves_the_cursor_on_the_last_good_page(self):
        api, s = self.bootstrap([raw_file("f1")])

        pages = [{"changes": [change("f2", file=raw_file("f2"))], "nextPageToken": "tok-p2"}]

        def flaky(page_token, *, page_size=1000):
            api.calls.append(f"changes:{page_token}")
            if pages:
                return pages.pop(0)
            raise TransportError("network")

        api.list_changes = flaky
        res = s.incremental_sync()
        self.assertEqual((res.status, res.error_class), ("error", "network"))
        self.assertEqual(self.store.cursor(), "tok-p2",
                         "the committed page is not replayed, the failed one is")
        self.assertEqual(self.live(), 2, "the committed page survives")

    def test_replaying_a_page_is_harmless(self):
        api, s = self.bootstrap([raw_file("f1")])
        page = {"changes": [change("f2", file=raw_file("f2"))], "newStartPageToken": "tok-1"}
        api.change_pages = [dict(page), dict(page)]
        s.incremental_sync()
        s.incremental_sync()
        self.assertEqual(self.live(), 2)

    def test_incremental_without_a_cursor_is_skipped_not_crashed(self):
        res = Syncer(self.store, FakeApi()).incremental_sync()
        self.assertEqual((res.status, res.error_class), ("skipped", "no_cursor"))

    def test_incremental_prunes_expired_tombstones(self):
        api, s = self.bootstrap([raw_file("f1")])
        self.store.tombstone_file("f1", now=0)  # long expired
        res = s.incremental_sync()
        self.assertEqual(res.pruned, 1)

    def test_a_payload_without_permissions_downgrades_to_unknown(self):
        """Deliberate, and the honest direction. If Drive stops showing us the
        permissions, the truthful answer is 'unknown', not the last state we
        happened to see -- keeping a stale 'private' would hide a share, and
        keeping a stale 'anyone_with_link' would invent one."""
        api, s = self.bootstrap([raw_file(
            "f1", permissions=[perm("user", "owner"), perm("anyone")])])
        self.assertEqual(self.conn.execute(
            "SELECT sharing_state FROM drive_files WHERE file_id='f1'").fetchone()[0],
            "anyone_with_link")
        api.change_pages = [{"changes": [change("f1", file=raw_file("f1"))],
                             "newStartPageToken": "tok-1"}]
        s.incremental_sync()
        self.assertEqual(self.conn.execute(
            "SELECT sharing_state FROM drive_files WHERE file_id='f1'").fetchone()[0], "unknown")

    def test_sharing_state_survives_a_round_trip_through_sync(self):
        api, s = self.bootstrap([raw_file(
            "f1", permissions=[perm("user", "owner"), perm("anyone", discoverable=True)])])
        row = self.conn.execute("SELECT * FROM drive_files WHERE file_id='f1'").fetchone()
        self.assertEqual(row["sharing_state"], "anyone_with_link")
        self.assertEqual(row["link_discoverable"], 1)


class RefreshTest(SyncTestCase):
    def test_refresh_updates_named_files_only(self):
        api = FakeApi([raw_file("f1", name="Before"), raw_file("f2", name="Other")])
        s = Syncer(self.store, api)
        s.initial_sync()
        api.files["f1"] = raw_file("f1", name="After")
        api.files["f2"] = raw_file("f2", name="Changed too")
        res = s.refresh_files(["f1"])
        self.assertEqual(res.updated, 1)
        self.assertEqual(self.name_of("f1"), "After")
        self.assertEqual(self.name_of("f2"), "Other", "only what was asked for")

    def test_refresh_tombstones_a_file_that_is_gone(self):
        api = FakeApi([raw_file("f1")])
        s = Syncer(self.store, api)
        s.initial_sync()
        del api.files["f1"]
        res = s.refresh_files(["f1"])
        self.assertEqual(res.deleted, 1)
        self.assertEqual(self.live(), 0)

    def test_refresh_fetches_concurrently_and_writes_on_one_thread(self):
        """Workers fetch; the calling thread writes. The SQLite connection is
        not thread-safe and must never be touched by a worker."""
        import threading
        api = FakeApi([raw_file(f"f{i}") for i in range(20)])
        s = Syncer(self.store, api, concurrency=4)
        s.initial_sync()
        writer_threads = set()
        real_upsert = self.store.upsert_file

        def spy(f, **kw):
            writer_threads.add(threading.current_thread().name)
            return real_upsert(f, **kw)

        self.store.upsert_file = spy
        s.refresh_files([f"f{i}" for i in range(20)])
        self.assertEqual(writer_threads, {threading.current_thread().name})

    def test_refresh_of_nothing_is_a_no_op(self):
        api = FakeApi()
        res = Syncer(self.store, api).refresh_files([])
        self.assertEqual((res.added, res.updated, res.deleted), (0, 0, 0))


class RateLimitTest(SyncTestCase):
    def test_rate_limiting_is_retried_with_backoff_not_treated_as_fatal(self):
        api = FakeApi([raw_file("f1")])
        attempts = []
        slept = []

        def flaky(**kw):
            attempts.append(1)
            if len(attempts) < 3:
                raise TransportError("rate_limited", 429)
            return {"files": [raw_file("f1")]}

        api.list_files = flaky
        s = Syncer(self.store, api, sleep=slept.append)
        res = s.initial_sync()
        self.assertEqual(res.status, "ok")
        self.assertEqual(len(slept), 2)
        self.assertLess(slept[0], slept[1], "backoff must widen")

    def test_persistent_rate_limiting_eventually_fails_the_run(self):
        api = FakeApi([raw_file("f1")])
        api.fail_list_with = "rate_limited"
        res = Syncer(self.store, api, sleep=lambda _s: None).initial_sync()
        self.assertEqual(res.status, "error")
        self.assertEqual(res.error_class, "rate_limited_persistent")


if __name__ == "__main__":
    unittest.main()
