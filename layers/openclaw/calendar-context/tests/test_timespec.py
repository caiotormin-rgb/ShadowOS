"""The all-day / timed split, and the window arithmetic that feeds the anchor."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from calctx.timespec import (WINDOW_BACK_MONTHS, WINDOW_FORWARD_MONTHS, TimeSpec,
                             TimeSpecError, add_months, date_range, day_bounds,
                             order_ts, parse_time, rfc3339, window_bounds, zone)
from tests.fixtures import NOW


class TimeSpecTest(unittest.TestCase):
    def test_a_spec_cannot_be_both_shapes(self):
        with self.assertRaises(TimeSpecError):
            TimeSpec("timed", utc=NOW, tz="UTC", date="2026-08-23")
        with self.assertRaises(TimeSpecError):
            TimeSpec("all_day", date="2026-08-23", utc=NOW)

    def test_a_timed_spec_must_carry_its_zone(self):
        """An instant without its original zone cannot render a local agenda."""
        with self.assertRaises(TimeSpecError):
            TimeSpec("timed", utc=NOW)

    def test_all_day_parses_as_a_date_and_never_as_an_instant(self):
        spec = parse_time({"date": "2026-08-23"}, calendar_tz="America/Sao_Paulo")
        self.assertEqual(spec.kind, "all_day")
        self.assertEqual(spec.date, "2026-08-23")
        self.assertIsNone(spec.utc, "a birthday is not an instant")

    def test_timed_keeps_the_zone_google_sent(self):
        spec = parse_time({"dateTime": "2026-08-23T10:00:00-03:00",
                           "timeZone": "America/Sao_Paulo"}, calendar_tz="UTC")
        self.assertEqual(spec.kind, "timed")
        self.assertEqual(spec.tz, "America/Sao_Paulo")
        self.assertEqual(spec.utc,
                         int(datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc).timestamp()))

    def test_missing_event_zone_falls_back_to_the_calendar_zone(self):
        """Google omits timeZone on most events and means the calendar's -- which
        is why calendarList (and its scope) is required rather than optional."""
        spec = parse_time({"dateTime": "2026-08-23T10:00:00+00:00"},
                          calendar_tz="Europe/Lisbon")
        self.assertEqual(spec.tz, "Europe/Lisbon")

    def test_a_naive_datetime_is_refused_rather_than_guessed(self):
        with self.assertRaises(TimeSpecError):
            parse_time({"dateTime": "2026-08-23T10:00:00"}, calendar_tz="UTC")

    def test_a_payload_with_neither_shape_is_none_not_a_guess(self):
        self.assertIsNone(parse_time({}, calendar_tz="UTC"))
        self.assertIsNone(parse_time(None, calendar_tz="UTC"))

    # -- the ordering aid ------------------------------------------------
    def test_order_ts_for_all_day_is_local_midnight_not_utc_midnight(self):
        ts = order_ts(TimeSpec("all_day", date="2026-08-23"), calendar_tz="America/Sao_Paulo")
        utc_midnight = int(datetime(2026, 8, 23, tzinfo=timezone.utc).timestamp())
        self.assertEqual(ts, utc_midnight + 3 * 3600, "Sao Paulo is UTC-3")

    def test_order_ts_for_timed_is_the_real_instant(self):
        self.assertEqual(order_ts(TimeSpec("timed", utc=NOW, tz="UTC"), calendar_tz="UTC"), NOW)

    def test_an_unknown_zone_degrades_to_utc_rather_than_killing_a_run(self):
        self.assertIs(zone("Mars/Olympus"), zone(None))

    # -- the window ------------------------------------------------------
    def test_window_is_three_months_back_and_twelve_forward(self):
        start, end = window_bounds(NOW)
        base = datetime.fromtimestamp(NOW, timezone.utc)
        self.assertEqual(start, int(add_months(base, -WINDOW_BACK_MONTHS).timestamp()))
        self.assertEqual(end, int(add_months(base, WINDOW_FORWARD_MONTHS).timestamp()))
        self.assertLess(start, NOW)
        self.assertGreater(end, NOW)

    def test_twelve_months_forward_covers_an_annual_series(self):
        """The stated reason for +12: a shorter horizon hides birthdays and
        renewals entirely, because their one instance falls outside it."""
        start, end = window_bounds(NOW)
        self.assertGreaterEqual(end - NOW, 365 * 86400 - 86400)

    def test_month_arithmetic_clamps_instead_of_overflowing(self):
        jan31 = datetime(2026, 1, 31, tzinfo=timezone.utc)
        self.assertEqual(add_months(jan31, 1).day, 28)
        self.assertEqual(add_months(datetime(2028, 1, 31, tzinfo=timezone.utc), 1).day, 29)

    def test_month_arithmetic_is_not_a_day_count(self):
        """365 days is not twelve months across a leap year, and the difference
        is exactly one annual occurrence falling outside the window."""
        base = datetime(2027, 3, 1, tzinfo=timezone.utc)
        self.assertEqual(add_months(base, 12).year, 2028)
        self.assertNotEqual(int(add_months(base, 12).timestamp()),
                            int(base.timestamp()) + 365 * 86400)

    def test_rfc3339_is_utc_with_a_z(self):
        self.assertTrue(rfc3339(NOW).endswith("Z"))
        self.assertRegex(rfc3339(0), r"^1970-01-01T00:00:00Z$")

    # -- Google's exclusive end date -------------------------------------
    def test_all_day_end_date_is_made_inclusive(self):
        """Google's end.date is exclusive; storing it as-is would make every
        one-day event report as spanning two."""
        self.assertEqual(date_range("2026-08-23", "2026-08-24"), ("2026-08-23", "2026-08-23"))
        self.assertEqual(date_range("2026-08-23", "2026-08-26"), ("2026-08-23", "2026-08-25"))

    def test_a_degenerate_end_date_does_not_go_backwards(self):
        self.assertEqual(date_range("2026-08-23", "2026-08-23")[1], "2026-08-23")

    def test_day_bounds_span_exactly_one_local_day(self):
        lo, hi = day_bounds("2026-08-23", calendar_tz="America/Sao_Paulo")
        self.assertEqual(hi - lo, 86400)


if __name__ == "__main__":
    unittest.main()
