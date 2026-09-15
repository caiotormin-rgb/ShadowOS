import sqlite3
import time
import unittest

from tests.fixtures import memory_conn

from mailctx.gmail import TransportError
from mailctx.store import Store, migrate
from mailctx.sync import DEFAULT_WINDOW_DAYS, Syncer, parse_message

# Inside the default 365-day window. A fixed epoch would drift out of the
# window as real time passes and make the prune look like a bug.
NOW = int(time.time()) - 86400


def raw(mid, thread=None, ts=NOW, subject="Hi", frm="a@x.invalid", labels=("INBOX",),
        snippet="", parts=None, history_id=100):
    payload = {"headers": [{"name": "From", "value": frm}, {"name": "To", "value": "me@x.invalid"},
                           {"name": "Subject", "value": subject}]}
    if parts:
        payload["parts"] = parts
    return {"id": mid, "threadId": thread or f"t-{mid}", "internalDate": str(ts * 1000),
            "labelIds": list(labels), "snippet": snippet, "historyId": str(history_id),
            "payload": payload}


class FakeApi:
    """Stands in for Gmail. Records calls so ordering can be asserted."""

    def __init__(self, messages=None, history_pages=None, history_id=1000):
        self.messages = {m["id"]: m for m in (messages or [])}
        self.history_pages = history_pages or []
        self.history_id = history_id
        self.calls = []
        self.fail_history_with = None

    def profile(self):
        self.calls.append("profile")
        return {"historyId": str(self.history_id), "emailAddress": "me@x.invalid"}

    def list_message_ids(self, *, query=None, page_token=None, max_results=500,
                         include_spam_trash=False):
        self.calls.append(f"list:{query}")
        return {"messages": [{"id": i} for i in self.messages]}

    def message_metadata(self, mid):
        self.calls.append(f"get:{mid}")
        if mid not in self.messages:
            raise TransportError("not_found", 404)
        return self.messages[mid]

    def history(self, start, *, page_token=None, max_results=500):
        self.calls.append(f"history:{start}")
        if self.fail_history_with:
            raise TransportError(self.fail_history_with, 404)
        return self.history_pages.pop(0) if self.history_pages else {"historyId": str(self.history_id)}


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.conn = memory_conn()
        migrate(self.conn)
        self.store = Store(self.conn)

    def count(self):
        return self.conn.execute(
            "SELECT COUNT(*) FROM mail_messages WHERE deleted_at IS NULL").fetchone()[0]

    # -- parsing ---------------------------------------------------------
    def test_parse_extracts_headers_and_timestamp(self):
        m = parse_message(raw("m1", subject="Dentist", frm="dr@x.invalid", ts=NOW))
        self.assertEqual(m.subject, "Dentist")
        self.assertEqual(m.from_addr, "dr@x.invalid")
        self.assertEqual(m.internal_ts, NOW)
        self.assertEqual(m.labels, ("INBOX",))

    def test_parse_collects_nested_attachment_metadata_only(self):
        parts = [{"filename": "", "mimeType": "multipart/mixed",
                  "parts": [{"filename": "bill.pdf", "mimeType": "application/pdf",
                             "body": {"size": 900, "data": "SHOULD-NOT-BE-READ"}}]}]
        m = parse_message(raw("m1", parts=parts))
        self.assertEqual(m.attachments, (("bill.pdf", "application/pdf", 900),))
        self.assertNotIn("SHOULD-NOT-BE-READ", repr(m))

    def test_parse_tolerates_missing_headers(self):
        m = parse_message({"id": "m1", "threadId": "t1", "payload": {}})
        self.assertEqual(m.subject, "")
        self.assertEqual(m.internal_ts, 0)

    # -- initial ---------------------------------------------------------
    def test_initial_sync_captures_cursor_before_listing(self):
        """Otherwise every change during the load is silently lost."""
        api = FakeApi([raw("m1"), raw("m2")], history_id=555)
        Syncer(self.store, api).initial_sync()
        self.assertEqual(api.calls[0], "profile", "cursor must be captured first")
        self.assertLess(api.calls.index("profile"),
                        api.calls.index(f"list:newer_than:{DEFAULT_WINDOW_DAYS}d"))
        self.assertEqual(
            self.conn.execute("SELECT last_history_id FROM mail_sync_state").fetchone()[0], 555)

    def test_initial_sync_indexes_everything(self):
        api = FakeApi([raw("m1"), raw("m2"), raw("m3")])
        res = Syncer(self.store, api).initial_sync()
        self.assertEqual((res.added, res.updated), (3, 0))
        self.assertEqual(self.count(), 3)

    def test_initial_sync_is_idempotent(self):
        api = FakeApi([raw("m1"), raw("m2")])
        s = Syncer(self.store, api)
        s.initial_sync()
        res = s.initial_sync()
        self.assertEqual((res.added, res.updated), (0, 0))
        self.assertEqual(self.count(), 2)

    def test_rerunning_an_interrupted_load_skips_what_is_already_stored(self):
        """A multi-hour lifetime load will be interrupted; resuming must be cheap."""
        api = FakeApi([raw(f"m{i}") for i in range(30)])
        s = Syncer(self.store, api, batch_size=10)
        s.initial_sync(max_messages=10)
        self.assertEqual(self.count(), 10)
        api.calls.clear()
        s2 = Syncer(self.store, api, batch_size=10)
        s2.initial_sync()
        self.assertEqual(self.count(), 30)
        self.assertEqual(s2.skipped, 10)
        fetched = [c for c in api.calls if c.startswith("get:")]
        self.assertEqual(len(fetched), 20, "already-stored messages must not be refetched")

    def test_incremental_never_skips_existing_messages(self):
        """The resume optimisation must not apply to changes we were told about."""
        api = FakeApi([raw("m1", labels=("INBOX", "UNREAD"))])
        s = Syncer(self.store, api)
        s.initial_sync()
        api.messages["m1"] = raw("m1", labels=("INBOX",))
        api.history_pages = [{"historyId": "2000", "history": [
            {"labelsRemoved": [{"message": {"id": "m1"}}]}]}]
        api.calls.clear()
        s.incremental_sync()
        self.assertIn("get:m1", api.calls, "a changed message must be refetched")

    def test_max_messages_bounds_a_validation_run(self):
        api = FakeApi([raw(f"m{i}") for i in range(50)])
        res = Syncer(self.store, api, batch_size=10).initial_sync(max_messages=20)
        self.assertEqual(self.count(), 20)
        self.assertEqual(res.added, 20)

    def test_message_vanishing_mid_run_is_not_fatal(self):
        api = FakeApi([raw("m1"), raw("m2")])
        api.messages["ghost"] = raw("ghost")
        del api.messages["ghost"]
        api.list_message_ids = lambda **kw: {"messages": [{"id": "m1"}, {"id": "ghost"}, {"id": "m2"}]}
        res = Syncer(self.store, api).initial_sync()
        self.assertEqual(res.status, "ok")
        self.assertEqual(self.count(), 2)

    def test_lifetime_window_uses_no_date_query_and_never_age_prunes(self):
        api = FakeApi([raw("ancient", ts=100)])
        s = Syncer(self.store, api, window_days=None)
        s.initial_sync()
        self.assertIn("list:None", api.calls, "lifetime sync must not send newer_than")
        self.assertEqual(s.window_start_ts(), 0)
        self.store.begin()
        pruned = self.store.prune(window_start_ts=s.window_start_ts())
        self.store.commit()
        self.assertEqual(pruned, 0, "a lifetime index must not prune by age")
        self.assertEqual(self.count(), 1)

    def test_transport_failure_records_class_and_preserves_index(self):
        api = FakeApi([raw("m1")])
        Syncer(self.store, api).initial_sync()
        api.profile = lambda: (_ for _ in ()).throw(TransportError("auth", 401))
        res = Syncer(self.store, api).initial_sync()
        self.assertEqual((res.status, res.error_class), ("error", "auth"))
        self.assertEqual(self.count(), 1, "a failed run must not destroy the last good index")

    # -- incremental -----------------------------------------------------
    def test_incremental_applies_adds_and_deletes(self):
        api = FakeApi([raw("m1")])
        s = Syncer(self.store, api)
        s.initial_sync()
        api.messages["m2"] = raw("m2")
        api.history_pages = [{"historyId": "2000", "history": [
            {"messagesAdded": [{"message": {"id": "m2"}}]},
            {"messagesDeleted": [{"message": {"id": "m1"}}]}]}]
        res = s.incremental_sync()
        self.assertEqual((res.added, res.deleted), (1, 1))
        self.assertEqual(self.count(), 1)
        self.assertEqual(
            self.conn.execute("SELECT last_history_id FROM mail_sync_state").fetchone()[0], 2000)

    def test_label_change_refetches_the_message(self):
        api = FakeApi([raw("m1", labels=("INBOX", "UNREAD"))])
        s = Syncer(self.store, api)
        s.initial_sync()
        api.messages["m1"] = raw("m1", labels=("INBOX",))
        api.history_pages = [{"historyId": "2000", "history": [
            {"labelsRemoved": [{"message": {"id": "m1"}}]}]}]
        s.incremental_sync()
        labels = [r[0] for r in self.conn.execute(
            "SELECT label FROM mail_labels WHERE message_id='m1'")]
        self.assertEqual(labels, ["INBOX"])

    def test_added_then_deleted_in_one_window_is_not_fetched_as_live(self):
        api = FakeApi([raw("m1")])
        s = Syncer(self.store, api)
        s.initial_sync()
        api.history_pages = [{"historyId": "2000", "history": [
            {"messagesAdded": [{"message": {"id": "gone"}}]},
            {"messagesDeleted": [{"message": {"id": "gone"}}]}]}]
        res = s.incremental_sync()
        self.assertEqual(res.added, 0)

    def test_stale_cursor_falls_back_to_resync(self):
        """Gmail keeps ~30 days of history; past that a 404 is expected, not fatal."""
        api = FakeApi([raw("m1")])
        s = Syncer(self.store, api)
        s.initial_sync()
        api.fail_history_with = "not_found"
        res = s.incremental_sync()
        self.assertTrue(res.resynced)
        self.assertEqual(res.kind, "initial")
        self.assertEqual(res.status, "ok")

    def test_incremental_prunes_messages_that_aged_out_of_the_window(self):
        """The window is enforced on every incremental run, not just at load."""
        stale_age = (DEFAULT_WINDOW_DAYS + 60) * 86400
        api = FakeApi([raw("fresh", ts=int(time.time()) - 86400),
                       raw("stale", ts=int(time.time()) - stale_age)])
        s = Syncer(self.store, api)
        s.initial_sync()
        self.assertEqual(self.count(), 2, "initial load stores what Gmail returned")
        api.history_pages = [{"historyId": "2000"}]
        res = s.incremental_sync()
        self.assertEqual(res.pruned, 1)
        rows = [r[0] for r in self.conn.execute("SELECT message_id FROM mail_messages")]
        self.assertEqual(rows, ["fresh"])

    def test_bounded_run_does_not_advance_the_cursor(self):
        """A --max validation run must not masquerade as a completed load.

        If it advanced the cursor, the next scheduled run would take the
        incremental branch and the unfetched remainder would never be
        backfilled -- an index stuck at 2,000 of 64,192 that reports healthy.
        """
        api = FakeApi([raw(f"m{i}") for i in range(30)])
        s = Syncer(self.store, api, batch_size=10)
        res = s.initial_sync(max_messages=10)
        self.assertTrue(res.truncated)
        self.assertIn("TRUNCATED", res.summary())
        self.assertIsNone(
            self.conn.execute("SELECT last_history_id FROM mail_sync_state").fetchone()[0])
        self.assertEqual(
            self.conn.execute("SELECT initial_complete FROM mail_sync_state").fetchone()[0], 0)
        self.assertEqual(s.sync().kind, "initial", "next run must resume the load")

    def test_a_stale_cursor_over_an_incomplete_index_still_resumes(self):
        """The exact state a live run was left in: a cursor from an earlier
        bounded load, plus a failed full load. Dispatching on cursor presence
        would go incremental and strand the unfetched remainder."""
        api = FakeApi([raw(f"m{i}") for i in range(20)])
        s = Syncer(self.store, api, batch_size=5)
        s.initial_sync(max_messages=5)
        # simulate the pre-fix database: cursor set, load never completed
        self.conn.execute("UPDATE mail_sync_state SET last_history_id = 12345, "
                          "initial_complete = 0, status = 'error'")
        self.assertEqual(s.sync().kind, "initial", "must resume, not go incremental")
        self.assertEqual(self.count(), 20)

    def test_complete_run_does_advance_the_cursor(self):
        api = FakeApi([raw("m1"), raw("m2")], history_id=777)
        s = Syncer(self.store, api)
        res = s.initial_sync()
        self.assertFalse(res.truncated)
        self.assertEqual(
            self.conn.execute("SELECT last_history_id FROM mail_sync_state").fetchone()[0], 777)
        self.assertEqual(
            self.conn.execute("SELECT initial_complete FROM mail_sync_state").fetchone()[0], 1)
        self.assertEqual(s.sync().kind, "incremental")

    def test_stale_cursor_recovery_is_visible_in_the_run_log(self):
        """`return` inside `except` still runs `finally`. Without guarding it,
        finish_run fires twice and the second call overwrites the resync row
        with status=ok -- every recovery silently indistinguishable from a
        normal run."""
        api = FakeApi([raw("m1")])
        s = Syncer(self.store, api)
        s.initial_sync()
        api.fail_history_with = "not_found"
        s.incremental_sync()
        rows = [dict(r) for r in self.conn.execute(
            "SELECT kind, status, error_class FROM mail_sync_runs ORDER BY run_id")]
        incremental = [r for r in rows if r["kind"] == "incremental"]
        self.assertEqual(len(incremental), 1)
        self.assertEqual(incremental[0]["status"], "resync")
        self.assertEqual(incremental[0]["error_class"], "stale_cursor")

    def test_incremental_without_cursor_is_skipped_not_crashed(self):
        res = Syncer(self.store, FakeApi()).incremental_sync()
        self.assertEqual((res.status, res.error_class), ("skipped", "no_cursor"))

    def test_sync_dispatches_initial_then_incremental(self):
        api = FakeApi([raw("m1")])
        s = Syncer(self.store, api)
        self.assertEqual(s.sync().kind, "initial")
        self.assertEqual(s.sync().kind, "incremental")


if __name__ == "__main__":
    unittest.main()


class ListingQueryTest(unittest.TestCase):
    """The listing query is the cheapest filter layer: ids for excluded mail
    are never returned, so they are never fetched, parsed, or stored."""

    @staticmethod
    def _q(window_days, exclude_query):
        from mailctx.sync import Syncer
        s = object.__new__(Syncer)
        s.window_days, s.exclude_query = window_days, exclude_query
        return s._query()

    def test_window_only(self):
        self.assertEqual(self._q(730, None), "newer_than:730d")

    def test_lifetime_has_no_window_term(self):
        self.assertIsNone(self._q(None, None))

    def test_lifetime_with_exclusion(self):
        self.assertEqual(self._q(None, "-category:promotions -category:social"),
                         "-category:promotions -category:social")

    def test_window_and_exclusion_combine(self):
        self.assertEqual(self._q(730, "-category:promotions"),
                         "newer_than:730d -category:promotions")
