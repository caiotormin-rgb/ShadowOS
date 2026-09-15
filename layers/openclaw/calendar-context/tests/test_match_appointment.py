"""The appointment-deduplication contract mail-context will consult.

Rules under test, straight from the plan: read-only and side-effect free; takes
a timed start or an all-day date but never a blend; matches by time proximity
first and optionally narrows by a fuzzy title; returns evidence with a reason
and a confidence band rather than a verdict; flags cancelled and declined
matches instead of counting them as attendance; and never answers confidently
from a stale index.

calendar-context owns this interface. Wiring mail-context to call it is a
separate, later change; nothing here reaches into that package.
"""
from __future__ import annotations

import unittest

from tests.fixtures import NOW, SOON, day_of
from tests.test_query import QueryTestBase

HOUR = 3600
DAY = 86400


class MatchAppointmentTest(QueryTestBase):
    def seed(self):
        self.add_timed("appt", SOON, summary="Dentist appointment", location="Clinic")
        self.add_timed("close", SOON + 40 * 60, summary="Physio session")
        self.add_timed("cancelled", SOON + 2 * DAY, summary="Dentist follow-up",
                       status="cancelled")
        self.add_timed("declined", SOON + 3 * DAY, summary="Team offsite",
                       response="declined")
        self.add_all_day("bday", day_of(SOON + 5 * DAY), summary="Sam birthday")

    def rows(self, **kw):
        return {r["instance_id"]: r for r in self.ctx().match_appointment(**kw).rows}

    # -- the shape of the question ---------------------------------------
    def test_a_blend_of_the_two_shapes_is_refused(self):
        """The same rule the schema enforces: an instant or a date, never both."""
        with self.assertRaises(ValueError):
            self.ctx().match_appointment(start=SOON, all_day_date=day_of(SOON))
        with self.assertRaises(ValueError):
            self.ctx().match_appointment()

    # -- time proximity --------------------------------------------------
    def test_an_exact_start_is_a_high_confidence_exact_match(self):
        row = self.rows(start=SOON)["appt"]
        self.assertEqual(row["match_reason"], "exact_start")
        self.assertEqual(row["confidence"], "high")
        self.assertEqual(row["delta_seconds"], 0)

    def test_a_ten_minute_miss_is_still_high_confidence(self):
        row = self.rows(start=SOON - 600)["appt"]
        self.assertEqual(row["match_reason"], "within_tolerance")
        self.assertEqual(row["confidence"], "high")
        self.assertEqual(row["delta_seconds"], 600)

    def test_a_forty_minute_miss_is_medium_because_emailed_times_are_approximate(self):
        row = self.rows(start=SOON)["close"]
        self.assertEqual(row["confidence"], "medium")
        self.assertEqual(row["delta_seconds"], 40 * 60)

    def test_tolerance_bounds_the_search(self):
        found = self.rows(start=SOON, tolerance_minutes=15)
        self.assertIn("appt", found)
        self.assertNotIn("close", found, "40 minutes is outside a 15-minute tolerance")

    def test_nothing_nearby_returns_nothing_rather_than_a_stretch(self):
        self.assertEqual(self.rows(start=SOON + 10 * HOUR), {})

    def test_an_overlapping_meeting_is_evidence_even_when_the_start_differs(self):
        """A candidate at 10:30 lands inside a 10:00-11:00 meeting."""
        row = self.rows(start=SOON + 30 * 60, tolerance_minutes=5).get("appt")
        self.assertIsNotNone(row)
        self.assertEqual(row["match_reason"], "overlap")

    # -- what must not count as scheduled --------------------------------
    def test_a_cancelled_occurrence_is_returned_flagged_not_hidden(self):
        row = self.rows(start=SOON + 2 * DAY)["cancelled"]
        self.assertIn("cancelled", row["caveats"])
        self.assertTrue(row["is_cancelled"])
        self.assertEqual(row["confidence"], "low",
                         "a cancelled occurrence must not read as already scheduled")

    def test_a_declined_invitation_is_flagged_rather_than_treated_as_attendance(self):
        row = self.rows(start=SOON + 3 * DAY)["declined"]
        self.assertIn("declined", row["caveats"])
        self.assertTrue(row["is_declined"])
        self.assertEqual(row["confidence"], "low")

    # -- all-day ---------------------------------------------------------
    def test_an_all_day_candidate_matches_an_all_day_event_by_date(self):
        row = self.rows(all_day_date=day_of(SOON + 5 * DAY))["bday"]
        self.assertEqual(row["match_reason"], "same_date")
        self.assertEqual(row["confidence"], "high")
        self.assertEqual(row["start_kind"], "all_day")
        self.assertNotIn("start_utc", row, "an all-day match is never returned as an instant")

    def test_an_all_day_candidate_also_sees_timed_events_that_day_but_weaker(self):
        found = self.rows(all_day_date=day_of(SOON))
        self.assertIn("appt", found)
        self.assertEqual(found["appt"]["match_reason"], "same_day")
        self.assertEqual(found["appt"]["confidence"], "medium")

    # -- the title hint --------------------------------------------------
    def test_a_matching_title_hint_is_recorded(self):
        row = self.rows(start=SOON, title_hint="dentist checkup")["appt"]
        self.assertIn("title_matched", row["caveats"])
        self.assertEqual(row["confidence"], "high")

    def test_a_mismatched_title_hint_demotes_rather_than_discards(self):
        """The hint narrows; it does not decide. A renamed meeting is still
        evidence that the slot is taken."""
        row = self.rows(start=SOON, title_hint="plumber")["appt"]
        self.assertIn("title_mismatch", row["caveats"])
        self.assertEqual(row["confidence"], "medium")

    # -- staleness -------------------------------------------------------
    def test_a_stale_index_never_produces_a_confident_match(self):
        ctx = self.ctx(now=NOW + 6 * HOUR)
        res = ctx.match_appointment(start=SOON)
        self.assertTrue(res.freshness.is_stale)
        for row in res.rows:
            self.assertEqual(row["confidence"], "low")
            self.assertIn("stale_index", row["caveats"])
        self.assertIn("STALE", res.freshness.describe())

    # -- evidence, not a verdict -----------------------------------------
    def test_the_result_is_evidence_and_carries_no_verdict_field(self):
        row = self.rows(start=SOON)["appt"]
        for key in ("match_reason", "confidence", "delta_seconds", "caveats",
                    "start_kind", "summary", "calendar_id"):
            self.assertIn(key, row)
        for verdict in ("already_scheduled", "suppress", "duplicate", "decision", "verdict"):
            self.assertNotIn(verdict, row,
                             "the decision to suppress a candidate belongs to the caller")

    def test_matching_writes_nothing(self):
        before = self.db.stat().st_mtime_ns
        ctx = self.ctx()
        ctx.match_appointment(start=SOON, title_hint="dentist")
        ctx.match_appointment(all_day_date=day_of(SOON + 5 * DAY))
        self.assertEqual(self.db.stat().st_mtime_ns, before,
                         "the check answers a question; it marks nothing")


if __name__ == "__main__":
    unittest.main()
