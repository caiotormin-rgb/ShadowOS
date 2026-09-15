import sqlite3
import tempfile
import unittest
from pathlib import Path

from calctx.query import CalendarContext, _fts_query
from calctx.store import Calendar, Event, Instance, Store, connect, migrate
from calctx.timespec import TimeSpec, order_ts
from tests.fixtures import (CAL_ID, LATER, NOW, SOON, WINDOW_END, WINDOW_START, day_of)

TZ = "America/Sao_Paulo"


def timed(ts, dur=3600):
    return TimeSpec("timed", utc=ts, tz=TZ), TimeSpec("timed", utc=ts + dur, tz=TZ)


class QueryTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "state" / "calendar-context.sqlite"
        conn = connect(self.db)
        migrate(conn)
        self.store = Store(conn)
        self.store.upsert_calendar(Calendar(CAL_ID, "Personal", TZ, True, True), now=NOW)
        self.seed()
        self.store.advance_global(window_start_ts=WINDOW_START, window_end_ts=WINDOW_END,
                                  kind="initial", now=NOW)
        self.store.start_run("initial", now=NOW)
        conn.close()

    def add_timed(self, event_id, ts, *, summary="Event", location="", dur=3600,
                  status="confirmed", transparency="opaque", response=None,
                  recurrence=(), kind="single"):
        s, e = timed(ts, dur)
        self.store.upsert_event(Event(CAL_ID, event_id, kind, s, e, summary=summary,
                                      location=location, transparency=transparency,
                                      self_response_status=response, status=status,
                                      recurrence=recurrence), now=NOW)
        self.store.replace_instances(CAL_ID, event_id, [
            Instance(CAL_ID, event_id, event_id, s, e, order_ts(s, calendar_tz=TZ),
                     status=status, summary=summary, location=location,
                     transparency=transparency, self_response_status=response)], now=NOW)

    def add_all_day(self, event_id, day, *, summary="Birthday"):
        s = TimeSpec("all_day", date=day)
        self.store.upsert_event(Event(CAL_ID, event_id, "single", s, s, summary=summary),
                                now=NOW)
        self.store.replace_instances(CAL_ID, event_id, [
            Instance(CAL_ID, event_id, event_id, s, s, order_ts(s, calendar_tz=TZ),
                     summary=summary)], now=NOW)

    def ctx(self, now=None, waiver_path=None):
        c = CalendarContext(self.db, now=(NOW + 60 if now is None else now),
                            waiver_path=waiver_path)
        self.addCleanup(c.close)
        return c

    def seed(self):
        raise NotImplementedError


class QueryTest(QueryTestBase):
    def seed(self):
        self.add_timed("e1", SOON, summary="Dentist appointment", location="Clinic")
        self.add_timed("e2", SOON + 1800, summary="Team sync")
        self.add_timed("t1", SOON + 600, summary="Focus block", transparency="transparent")
        self.add_timed("d1", SOON + 900, summary="Optional workshop", response="declined")
        self.add_timed("c1", LATER, summary="Cancelled thing", status="cancelled")
        self.add_all_day("b1", day_of(LATER), summary="Birthday")

    # -- the boundary ----------------------------------------------------
    def test_created_database_is_private(self):
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.db.parent.stat().st_mode & 0o777, 0o700)

    def test_surface_is_physically_read_only(self):
        c = self.ctx()
        with self.assertRaises(sqlite3.OperationalError):
            c._conn.execute("DELETE FROM cal_instances")
        with self.assertRaises(sqlite3.OperationalError):
            c._conn.execute("UPDATE cal_events SET summary = 'x'")

    # -- agenda ----------------------------------------------------------
    def test_agenda_is_chronological_and_hides_cancelled_and_declined(self):
        rows = self.ctx().agenda(SOON - 60, LATER + 86400).rows
        self.assertEqual([r["instance_id"] for r in rows], ["e1", "t1", "e2", "b1"])

    def test_declined_can_be_asked_for_explicitly(self):
        rows = self.ctx().agenda(SOON - 60, LATER + 86400, include_declined=True).rows
        self.assertIn("d1", [r["instance_id"] for r in rows])

    def test_a_cancelled_occurrence_is_visible_when_asked_for_and_flagged(self):
        rows = self.ctx().agenda(LATER - 60, LATER + 86400, include_cancelled=True).rows
        cancelled = [r for r in rows if r["instance_id"] == "c1"]
        self.assertEqual(len(cancelled), 1)
        self.assertTrue(cancelled[0]["is_cancelled"])

    def test_an_all_day_row_never_comes_back_as_an_instant(self):
        row = [r for r in self.ctx().agenda(SOON - 60, LATER + 86400).rows
               if r["instance_id"] == "b1"][0]
        self.assertEqual(row["start_kind"], "all_day")
        self.assertEqual(row["start_date"], day_of(LATER))
        self.assertNotIn("start_utc", row)
        self.assertNotIn("start_local", row)

    def test_the_ordering_aid_never_leaves_the_module(self):
        """order_ts is local midnight for an all-day row. Returning it would
        hand a caller an instant that a birthday never had."""
        for row in self.ctx().agenda(SOON - 60, LATER + 86400).rows:
            self.assertNotIn("order_ts", row)

    def test_a_timed_row_keeps_its_original_zone_and_renders_locally(self):
        row = [r for r in self.ctx().agenda(SOON - 60, LATER + 86400).rows
               if r["instance_id"] == "e1"][0]
        self.assertEqual(row["start_kind"], "timed")
        self.assertEqual(row["start_tz"], TZ)
        self.assertIn("-03:00", row["start_local"])
        self.assertNotIn("start_date", row)

    def test_agenda_truncation_is_reported(self):
        res = self.ctx().agenda(SOON - 60, LATER + 86400, limit=1)
        self.assertEqual(len(res.rows), 1)
        self.assertTrue(res.truncated)

    def test_limit_is_capped(self):
        self.assertLessEqual(len(self.ctx().agenda(0, WINDOW_END, limit=10_000).rows), 200)

    # -- search ----------------------------------------------------------
    def test_search_matches_title_and_location(self):
        self.assertEqual([r["instance_id"] for r in self.ctx().search("dentist").rows], ["e1"])
        self.assertEqual([r["instance_id"] for r in self.ctx().search("clinic").rows], ["e1"])

    def test_fts_operators_in_untrusted_titles_are_neutralised(self):
        """Anyone who can send an invitation can write a meeting title."""
        for hostile in ['dentist OR sync', 'summary:"x"', 'NEAR(a b)', '"', 'a*']:
            with self.subTest(hostile=hostile):
                self.ctx().search(hostile)   # must not raise
        self.assertEqual(_fts_query("dentist OR sync"), '"dentist" "OR" "sync"')
        self.assertEqual(self.ctx().search("dentist OR sync").rows, [],
                         "OR must be a literal token, not an operator")

    def test_search_excludes_cancelled_by_default(self):
        self.assertEqual(self.ctx().search("cancelled").rows, [])
        self.assertEqual(len(self.ctx().search("cancelled", include_cancelled=True).rows), 1)

    # -- conflicts -------------------------------------------------------
    def test_conflicts_finds_real_overlaps_only(self):
        rows = self.ctx().conflicts(SOON - 60, LATER + 86400).rows
        pairs = {(r["a_instance_id"], r["b_instance_id"]) for r in rows}
        self.assertIn(("e1", "e2"), pairs)
        self.assertEqual(rows[0]["overlap_seconds"], 1800)

    def test_transparent_and_declined_events_do_not_conflict(self):
        pairs = {(r["a_instance_id"], r["b_instance_id"])
                 for r in self.ctx().conflicts(SOON - 60, LATER + 86400).rows}
        for pair in pairs:
            self.assertNotIn("t1", pair, "a 'free' event does not occupy the user")
            self.assertNotIn("d1", pair, "a declined invitation is not attendance")

    def test_an_all_day_event_never_conflicts_with_a_meeting(self):
        pairs = {(r["a_instance_id"], r["b_instance_id"])
                 for r in self.ctx().conflicts(0, WINDOW_END).rows}
        for pair in pairs:
            self.assertNotIn("b1", pair, "a birthday is not an instant and blocks nothing")

    # -- event -----------------------------------------------------------
    def test_event_returns_the_rule_verbatim_and_does_not_expand_it(self):
        conn = connect(self.db)
        Store(conn).upsert_event(
            Event(CAL_ID, "s1", "series", *timed(SOON),
                  summary="Standup", recurrence=["RRULE:FREQ=WEEKLY;BYDAY=MO;BYSETPOS=1"]),
            now=NOW)
        conn.close()
        row = self.ctx().event("s1").rows[0]
        self.assertEqual(row["recurrence"], ["RRULE:FREQ=WEEKLY;BYDAY=MO;BYSETPOS=1"])
        self.assertEqual(row["instances"], [], "a query must never run an RRULE engine")

    def test_event_carries_its_attendees(self):
        conn = connect(self.db)
        Store(conn).set_attendees(CAL_ID, "e1", [("a@x.invalid", "accepted", False, False)])
        conn.close()
        row = self.ctx().event("e1", CAL_ID).rows[0]
        self.assertEqual(row["attendees"],
                         [{"email": "a@x.invalid", "response_status": "accepted",
                           "is_self": False, "optional": False}])

    # -- freshness -------------------------------------------------------
    def test_every_response_carries_freshness(self):
        c = self.ctx()
        for res in (c.agenda(SOON - 60, LATER), c.search("dentist"), c.event("e1"),
                    c.conflicts(SOON - 60, LATER), c.status(),
                    c.match_appointment(start=SOON)):
            self.assertEqual(res.freshness.age_seconds, 60)
            self.assertFalse(res.freshness.is_stale)
            self.assertEqual(res.freshness.window_start_ts, WINDOW_START)
            self.assertEqual(res.freshness.window_end_ts, WINDOW_END,
                             "a calendar window is bounded on both sides")

    def test_a_stale_index_says_so(self):
        res = self.ctx(now=NOW + 4 * 3600).agenda(SOON - 60, LATER)
        self.assertTrue(res.freshness.is_stale)
        self.assertIn("STALE", res.freshness.describe())

    def test_a_never_synced_index_is_stale_not_silent(self):
        empty = Path(self.tmp.name) / "empty" / "db.sqlite"
        conn = connect(empty)
        migrate(conn)
        conn.close()
        with CalendarContext(empty, now=NOW, waiver_path=None) as c:
            res = c.status()
        self.assertTrue(res.freshness.is_stale)
        self.assertIn("never synchronized", res.freshness.describe())

    # -- status ----------------------------------------------------------
    def test_status_counts_and_declares_no_write_path(self):
        row = self.ctx().status().rows[0]
        self.assertEqual(row["calendars"], 1)
        self.assertEqual(row["instances"], 6)
        self.assertEqual(row["cancelled"], 1)
        self.assertEqual(row["write_path"], "none")
        self.assertEqual(row["last_run"]["kind"], "initial")

    def test_an_operator_waiver_is_surfaced_in_every_status_response(self):
        """The plan's condition for accepting a waiver is that it cannot be
        quietly forgotten."""
        waiver = Path(self.tmp.name) / "encryption-waiver.txt"
        waiver.write_text("Operator waiver: stationary workstation, LUKS deferred.\n")
        row = self.ctx(waiver_path=waiver).status().rows[0]
        self.assertTrue(row["encryption_waiver"]["active"])
        self.assertIn("Operator waiver", row["encryption_waiver"]["reason"])

    def test_no_waiver_reads_as_no_waiver(self):
        missing = Path(self.tmp.name) / "nope.txt"
        self.assertFalse(self.ctx(waiver_path=missing).status().rows[0]
                         ["encryption_waiver"]["active"])


if __name__ == "__main__":
    unittest.main()
