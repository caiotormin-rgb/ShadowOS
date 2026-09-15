"""End to end over a real file-backed index: sync writes, the contract reads.

The unit tests seed the store directly, which is fast but proves only that each
layer works against its own idea of the schema. This one drives a full sync
through the real transport (with a synthetic opener), then opens the resulting
database read-only through the agent-facing contract -- the same file, the same
CHECK constraints, the same FTS triggers.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from calctx.gcal import CalendarReadOnly
from calctx.query import CalendarContext
from calctx.store import Store, connect, migrate
from calctx.sync import Syncer
from calctx.timespec import window_bounds
from tests.fixtures import (CAL_ID, NOW, SOON, day_of, raw_all_day, raw_calendar,
                            raw_instance, raw_timed, self_attendee)

WINDOW = window_bounds(NOW)
TZ = "America/Sao_Paulo"


class FakeTokens:
    def bearer(self):
        return "ya29.FAKE"


class Opener:
    """A synthetic network. Routes by URL, like the real thing does by path."""

    def __init__(self):
        self.methods = set()

    def __call__(self, method, url, headers, timeout):
        self.methods.add(method)
        path = url.split("?")[0]
        if path.endswith("/calendarList"):
            body = {"items": [raw_calendar(CAL_ID, tz=TZ)]}
        elif path.endswith("/instances"):
            body = {"items": [
                raw_instance("s1_0", start=SOON, summary="Standup", tz=TZ),
                raw_instance("s1_1", start=SOON + 86400, summary="Standup", tz=TZ,
                             status="cancelled"),
                raw_instance("s1_2", start=SOON + 2 * 86400 + 1800,
                             summary="Standup (moved)", tz=TZ)]}
        else:
            body = {"items": [
                raw_timed("e1", start=SOON + 300, summary="Dentist appointment",
                          location="Clinic", tz=TZ, attendees=[self_attendee("accepted")]),
                raw_timed("s1", summary="Standup", tz=TZ,
                          recurrence=["RRULE:FREQ=DAILY;COUNT=3"]),
                raw_timed("s1_2", summary="Standup (moved)", start=SOON + 2 * 86400 + 1800,
                          tz=TZ, recurring_event_id="s1",
                          original_start=SOON + 2 * 86400),
                raw_all_day("b1", day=day_of(SOON), summary="Sam birthday")],
                "nextSyncToken": "tok-1"}
        return 200, json.dumps(body).encode()


class IntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "state" / "calendar-context.sqlite"
        self.opener = Opener()
        conn = connect(self.db)
        migrate(conn)
        api = CalendarReadOnly(FakeTokens(), opener=self.opener)
        self.res = Syncer(Store(conn), api, now=NOW, concurrency=2).sync()
        conn.close()

    def ctx(self, now=NOW + 60):
        c = CalendarContext(self.db, now=now, waiver_path=None)
        self.addCleanup(c.close)
        return c

    def test_the_run_succeeded_using_get_only(self):
        self.assertEqual(self.res.status, "ok")
        self.assertEqual(self.opener.methods, {"GET"})

    def test_the_index_holds_series_overrides_and_all_day_together(self):
        row = self.ctx().status().rows[0]
        self.assertEqual(row["calendars"], 1)
        self.assertEqual(row["series"], 1)
        self.assertEqual(row["instances"], 5)   # e1, b1, s1_0, s1_1, s1_2
        self.assertEqual(row["cancelled"], 1)

    def test_an_agenda_interleaves_dates_and_instants_without_confusing_them(self):
        rows = self.ctx().agenda(WINDOW[0], WINDOW[1]).rows
        kinds = {r["instance_id"]: r["start_kind"] for r in rows}
        self.assertEqual(kinds["b1"], "all_day")
        self.assertEqual(kinds["e1"], "timed")
        self.assertNotIn("s1_1", kinds, "a cancelled occurrence is not on the agenda")
        for row in rows:
            self.assertNotIn("order_ts", row)
            if row["start_kind"] == "all_day":
                self.assertNotIn("start_utc", row)

    def test_the_cancelled_occurrence_survives_as_a_distinguishable_row(self):
        rows = self.ctx().agenda(WINDOW[0], WINDOW[1], include_cancelled=True).rows
        cancelled = [r for r in rows if r["instance_id"] == "s1_1"]
        self.assertEqual(len(cancelled), 1)
        self.assertTrue(cancelled[0]["is_cancelled"])

    def test_the_override_carries_its_own_title_and_time(self):
        row = [r for r in self.ctx().agenda(WINDOW[0], WINDOW[1]).rows
               if r["instance_id"] == "s1_2"][0]
        self.assertEqual(row["summary"], "Standup (moved)")
        self.assertEqual(row["is_override"], 1)
        self.assertEqual(row["start_utc"], SOON + 2 * 86400 + 1800)

    def test_the_rule_is_readable_but_never_expanded_at_read_time(self):
        row = self.ctx().event("s1", CAL_ID).rows[0]
        self.assertEqual(row["recurrence"], ["RRULE:FREQ=DAILY;COUNT=3"])
        self.assertEqual(len(row["instances"]), 3, "expansion happened at sync time")

    def test_search_reaches_the_materialized_layer(self):
        self.assertEqual([r["instance_id"] for r in self.ctx().search("dentist").rows], ["e1"])
        self.assertEqual([r["instance_id"] for r in self.ctx().search("clinic").rows], ["e1"])

    def test_match_appointment_finds_the_real_appointment(self):
        rows = self.ctx().match_appointment(start=SOON + 300, title_hint="dentist").rows
        top = rows[0]
        self.assertEqual(top["instance_id"], "e1")
        self.assertEqual(top["match_reason"], "exact_start")
        self.assertEqual(top["confidence"], "high")
        self.assertIn("title_matched", top["caveats"])

    def test_attendees_survive_the_round_trip_but_never_a_description(self):
        row = self.ctx().event("e1", CAL_ID).rows[0]
        self.assertEqual([a["email"] for a in row["attendees"]], ["me@example.invalid"])
        self.assertNotIn("description", row)

    def test_a_second_run_over_the_same_data_changes_nothing(self):
        conn = connect(self.db)
        api = CalendarReadOnly(FakeTokens(), opener=self.opener)
        res = Syncer(Store(conn), api, now=NOW, concurrency=2).sync()
        conn.close()
        self.assertEqual((res.added, res.deleted), (0, 0))
        self.assertEqual(self.ctx().status().rows[0]["instances"], 5)


if __name__ == "__main__":
    unittest.main()
