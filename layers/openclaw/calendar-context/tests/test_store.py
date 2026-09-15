import unittest

from calctx.store import Calendar, Event, Instance, Store, migrate
from calctx.timespec import TimeSpec, order_ts
from tests.fixtures import (ANCIENT, BEYOND, CAL_ID, NOW, PAST, SOON, WINDOW_END,
                            WINDOW_START, memory_conn)


def timed(ts: int, dur: int = 3600) -> tuple[TimeSpec, TimeSpec]:
    return TimeSpec("timed", utc=ts, tz="UTC"), TimeSpec("timed", utc=ts + dur, tz="UTC")


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.conn = memory_conn()
        migrate(self.conn)
        self.store = Store(self.conn)
        self.store.upsert_calendar(Calendar(CAL_ID, "Personal", "UTC"), now=NOW)

    def event(self, event_id="e1", *, start=SOON, kind="single", etag='"v1"',
              summary="Standup", status="confirmed", recurrence=(), **kw) -> Event:
        s, e = timed(start)
        return Event(calendar_id=CAL_ID, event_id=event_id, kind=kind, start=s, end=e,
                     etag=etag, summary=summary, status=status, recurrence=recurrence, **kw)

    def instance(self, instance_id="e1", *, series="e1", start=SOON, status="confirmed",
                 summary="Standup") -> Instance:
        s, e = timed(start)
        return Instance(calendar_id=CAL_ID, instance_id=instance_id, series_event_id=series,
                        start=s, end=e, order_ts=order_ts(s, calendar_tz="UTC"),
                        status=status, summary=summary)

    # -- calendars -------------------------------------------------------
    def test_calendar_upsert_reports_added_then_updated(self):
        self.assertEqual(self.store.upsert_calendar(Calendar("x@y.invalid"), now=NOW), "added")
        self.assertEqual(self.store.upsert_calendar(Calendar("x@y.invalid"), now=NOW), "updated")

    def test_only_user_selected_calendars_are_in_scope(self):
        self.store.upsert_calendar(Calendar("other@y.invalid", selected=False), now=NOW)
        self.assertEqual([r["calendar_id"] for r in self.store.selected_calendars()], [CAL_ID])

    def test_tombstoned_calendar_leaves_scope(self):
        self.assertTrue(self.store.tombstone_calendar(CAL_ID, now=NOW))
        self.assertEqual(self.store.selected_calendars(), [])
        self.assertFalse(self.store.tombstone_calendar(CAL_ID, now=NOW), "second is a no-op")

    # -- events ----------------------------------------------------------
    def test_upsert_reports_added_then_updated(self):
        self.assertEqual(self.store.upsert_event(self.event(), now=NOW), "added")
        self.assertEqual(self.store.upsert_event(self.event(), now=NOW), "updated")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM cal_events").fetchone()[0], 1)

    def test_replaying_the_same_page_is_idempotent(self):
        """A replayed events.list page must not duplicate or corrupt rows."""
        batch = [self.event("e1"), self.event("e2", start=SOON + 7200)]
        for _ in range(3):
            for ev in batch:
                self.store.upsert_event(ev, now=NOW)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM cal_events").fetchone()[0], 2)

    def test_recurrence_is_stored_verbatim(self):
        rule = ["RRULE:FREQ=MONTHLY;BYDAY=-1FR;BYSETPOS=1", "EXDATE;TZID=UTC:20260828T120000"]
        self.store.upsert_event(self.event("s1", kind="series", recurrence=rule), now=NOW)
        stored = self.conn.execute(
            "SELECT recurrence FROM cal_events WHERE event_id='s1'").fetchone()[0]
        import json
        self.assertEqual(json.loads(stored), rule, "an RRULE must never be normalized")

    def test_attendee_changes_are_applied_not_accumulated(self):
        self.store.upsert_event(self.event(attendees=(("a@x.invalid", "accepted", False, False),
                                                      ("me@x.invalid", "needsAction", True, False))),
                                now=NOW)
        self.store.upsert_event(self.event(attendees=(("me@x.invalid", "declined", True, False),)),
                                now=NOW)
        rows = list(self.conn.execute(
            "SELECT email, response_status FROM cal_attendees ORDER BY email"))
        self.assertEqual([(r["email"], r["response_status"]) for r in rows],
                         [("me@x.invalid", "declined")])

    def test_tombstoning_an_event_removes_its_occurrences(self):
        """A deleted event is not the same thing as a cancelled occurrence."""
        self.store.upsert_event(self.event(), now=NOW)
        self.store.replace_instances(CAL_ID, "e1", [self.instance()], now=NOW)
        self.assertTrue(self.store.tombstone_event(CAL_ID, "e1", now=NOW))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM cal_instances").fetchone()[0], 0)
        self.assertFalse(self.store.tombstone_event(CAL_ID, "e1", now=NOW))

    def test_a_resurrected_event_clears_its_tombstone(self):
        self.store.upsert_event(self.event(), now=NOW)
        self.store.tombstone_event(CAL_ID, "e1", now=NOW)
        self.store.upsert_event(self.event(), now=NOW)
        self.assertIsNone(self.store.event_row(CAL_ID, "e1")["deleted_at"])

    # -- instances -------------------------------------------------------
    def test_replace_is_a_replace_not_a_merge(self):
        """A shortened RRULE must not leave orphan occurrences behind; they
        would read exactly like real meetings."""
        self.store.upsert_event(self.event("s1", kind="series", recurrence=["RRULE:FREQ=DAILY"]),
                                now=NOW)
        self.store.replace_instances(CAL_ID, "s1", [
            self.instance("s1_0", series="s1", start=SOON),
            self.instance("s1_1", series="s1", start=SOON + 86400),
            self.instance("s1_2", series="s1", start=SOON + 2 * 86400)], now=NOW)
        self.store.replace_instances(CAL_ID, "s1", [
            self.instance("s1_0", series="s1", start=SOON)], now=NOW)
        ids = [r[0] for r in self.conn.execute(
            "SELECT instance_id FROM cal_instances ORDER BY instance_id")]
        self.assertEqual(ids, ["s1_0"])

    def test_a_cancelled_occurrence_is_stored_not_deleted(self):
        """'cancelled' and 'never existed' answer 'am I free Thursday?'
        differently, so the row has to survive."""
        self.store.upsert_event(self.event("s1", kind="series", recurrence=["RRULE:FREQ=DAILY"]),
                                now=NOW)
        self.store.replace_instances(CAL_ID, "s1", [
            self.instance("s1_0", series="s1", start=SOON),
            self.instance("s1_1", series="s1", start=SOON + 86400, status="cancelled")], now=NOW)
        rows = dict(self.conn.execute(
            "SELECT instance_id, status FROM cal_instances").fetchall())
        self.assertEqual(rows["s1_1"], "cancelled")

    def test_fts_follows_instance_replacement(self):
        self.store.upsert_event(self.event("s1", kind="series", recurrence=["RRULE:FREQ=DAILY"]),
                                now=NOW)
        self.store.replace_instances(CAL_ID, "s1", [
            self.instance("s1_0", series="s1", summary="Dentist")], now=NOW)
        hits = self.conn.execute(
            "SELECT COUNT(*) FROM cal_search WHERE cal_search MATCH 'Dentist'").fetchone()[0]
        self.assertEqual(hits, 1)
        self.store.replace_instances(CAL_ID, "s1", [], now=NOW)
        hits = self.conn.execute(
            "SELECT COUNT(*) FROM cal_search WHERE cal_search MATCH 'Dentist'").fetchone()[0]
        self.assertEqual(hits, 0, "a removed occurrence must leave the search index too")

    # -- retention -------------------------------------------------------
    def test_prune_enforces_both_window_edges(self):
        for eid, start in (("old", ANCIENT), ("now", SOON), ("far", BEYOND)):
            self.store.upsert_event(self.event(eid, start=start), now=NOW)
            self.store.replace_instances(CAL_ID, eid, [self.instance(eid, series=eid, start=start)],
                                         now=NOW)
        pruned = self.store.prune(window_start_ts=WINDOW_START, window_end_ts=WINDOW_END)
        self.assertEqual(pruned, 2, "a calendar window is bounded on both sides")
        self.assertEqual([r[0] for r in self.conn.execute(
            "SELECT instance_id FROM cal_instances")], ["now"])

    def test_a_series_master_survives_while_it_has_an_in_window_instance(self):
        self.store.upsert_event(self.event("s1", kind="series", start=ANCIENT,
                                           recurrence=["RRULE:FREQ=DAILY"]), now=NOW)
        self.store.replace_instances(CAL_ID, "s1", [
            self.instance("s1_0", series="s1", start=SOON)], now=NOW)
        self.store.prune(window_start_ts=WINDOW_START, window_end_ts=WINDOW_END)
        self.assertIsNotNone(self.store.event_row(CAL_ID, "s1"),
                             "a master that started before the window still owns occurrences in it")

    def test_an_override_survives_while_its_parent_does(self):
        """Pruning the override row would silently reinstate the original time
        at the next re-materialization."""
        self.store.upsert_event(self.event("s1", kind="series", start=SOON,
                                           recurrence=["RRULE:FREQ=DAILY"]), now=NOW)
        self.store.upsert_event(self.event("s1_ovr", kind="override", start=SOON + 3600,
                                           recurring_event_id="s1"), now=NOW)
        self.store.replace_instances(CAL_ID, "s1", [
            self.instance("s1_ovr", series="s1", start=SOON + 3600)], now=NOW)
        self.store.prune(window_start_ts=WINDOW_START, window_end_ts=WINDOW_END)
        self.assertIsNotNone(self.store.event_row(CAL_ID, "s1_ovr"))

    def test_a_series_with_nothing_left_in_the_window_is_dropped(self):
        self.store.upsert_event(self.event("gone", start=ANCIENT), now=NOW)
        self.store.replace_instances(CAL_ID, "gone", [
            self.instance("gone", series="gone", start=ANCIENT)], now=NOW)
        self.store.mark_materialized(CAL_ID, "gone", '"v1"', now=NOW)
        self.store.prune(window_start_ts=WINDOW_START, window_end_ts=WINDOW_END, now=NOW)
        self.assertIsNone(self.store.event_row(CAL_ID, "gone"))

    def test_an_unexpanded_event_survives_prune(self):
        """Otherwise prune wipes the progress of an interrupted initial load on
        the very next run -- 'no occurrences yet' is not 'no occurrences'."""
        self.store.upsert_event(self.event("pending", start=SOON), now=NOW)
        self.store.prune(window_start_ts=WINDOW_START, window_end_ts=WINDOW_END, now=NOW)
        self.assertIsNotNone(self.store.event_row(CAL_ID, "pending"))

    def test_a_fresh_tombstone_is_kept_for_reconciliation_then_expires(self):
        self.store.upsert_event(self.event("del", start=SOON), now=NOW)
        self.store.mark_materialized(CAL_ID, "del", '"v1"', now=NOW)
        self.store.tombstone_event(CAL_ID, "del", now=NOW)
        self.store.prune(window_start_ts=WINDOW_START, window_end_ts=WINDOW_END, now=NOW)
        self.assertIsNotNone(self.store.event_row(CAL_ID, "del"))
        self.store.prune(window_start_ts=WINDOW_START, window_end_ts=WINDOW_END,
                         now=NOW + 30 * 86400)
        self.assertIsNone(self.store.event_row(CAL_ID, "del"))

    # -- cursors ---------------------------------------------------------
    def test_cursor_carries_the_window_that_minted_it(self):
        self.store.advance_calendar_cursor(CAL_ID, "tok-1", anchor_start_ts=WINDOW_START,
                                           anchor_end_ts=WINDOW_END, now=NOW)
        row = self.store.calendar_cursor(CAL_ID)
        self.assertEqual((row["sync_token"], row["anchor_start_ts"], row["anchor_end_ts"]),
                         ("tok-1", WINDOW_START, WINDOW_END))
        self.assertEqual(row["status"], "ok")

    def test_clearing_an_expired_token_keeps_the_indexed_data(self):
        self.store.upsert_event(self.event(), now=NOW)
        self.store.advance_calendar_cursor(CAL_ID, "tok-1", anchor_start_ts=WINDOW_START,
                                           anchor_end_ts=WINDOW_END, now=NOW)
        self.store.clear_calendar_token(CAL_ID)
        self.assertIsNone(self.store.calendar_cursor(CAL_ID)["sync_token"])
        self.assertIsNotNone(self.store.event_row(CAL_ID, "e1"))

    def test_one_calendars_failure_does_not_disturb_another(self):
        self.store.upsert_calendar(Calendar("other@y.invalid"), now=NOW)
        self.store.advance_calendar_cursor(CAL_ID, "tok-1", anchor_start_ts=WINDOW_START,
                                           anchor_end_ts=WINDOW_END, now=NOW)
        self.store.record_calendar_failure("other@y.invalid", "forbidden_or_quota")
        good = self.store.calendar_cursor(CAL_ID)
        bad = self.store.calendar_cursor("other@y.invalid")
        self.assertEqual((good["status"], good["sync_token"]), ("ok", "tok-1"))
        self.assertEqual((bad["status"], bad["error_class"]), ("error", "forbidden_or_quota"))

    def test_failure_is_recorded_without_losing_the_last_good_window(self):
        self.store.advance_global(window_start_ts=WINDOW_START, window_end_ts=WINDOW_END,
                                  kind="initial", now=NOW)
        self.store.record_failure("auth")
        state = self.conn.execute("SELECT * FROM cal_sync_state").fetchone()
        self.assertEqual((state["status"], state["error_class"]), ("error", "auth"))
        self.assertEqual(state["window_start_ts"], WINDOW_START)
        self.assertEqual(state["last_full_sync_at"], NOW)

    def test_incremental_advance_is_not_a_full_sync(self):
        self.store.advance_global(window_start_ts=WINDOW_START, window_end_ts=WINDOW_END, now=NOW)
        self.assertIsNone(
            self.conn.execute("SELECT last_full_sync_at FROM cal_sync_state").fetchone()[0])

    # -- transactions ----------------------------------------------------
    def test_abort_only_rolls_back_when_a_transaction_is_open(self):
        """An error handler must be able to record a failure whether or not it
        was reached with a transaction already open; BEGIN inside one is an
        error, and that crash would hide the original failure."""
        self.assertFalse(self.store.abort())
        self.store.begin()
        self.assertTrue(self.store.abort())
        self.store.begin()       # must not raise "cannot start a transaction within"
        self.store.commit()

    def test_rollback_leaves_no_partial_page(self):
        self.store.begin()
        self.store.upsert_event(self.event("rb"), now=NOW)
        self.store.rollback()
        self.assertIsNone(self.store.event_row(CAL_ID, "rb"))

    # -- resume bookkeeping ----------------------------------------------
    def test_materialized_state_reports_etag_and_count(self):
        self.store.upsert_event(self.event("s1", kind="series", etag='"v1"',
                                           recurrence=["RRULE:FREQ=DAILY"]), now=NOW)
        self.assertEqual(self.store.materialized_state(CAL_ID, "s1"), (None, 0))
        self.store.replace_instances(CAL_ID, "s1", [self.instance("s1_0", series="s1")], now=NOW)
        self.store.mark_materialized(CAL_ID, "s1", '"v1"', now=NOW)
        self.assertEqual(self.store.materialized_state(CAL_ID, "s1"), ('"v1"', 1))


if __name__ == "__main__":
    unittest.main()
