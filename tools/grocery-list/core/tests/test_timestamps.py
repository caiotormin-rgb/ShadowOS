"""Timestamps the engine accepts must be ones SQLite can read.

QA L2: retention compares instants with SQLite's unixepoch(), which returns
NULL for an offset without a colon or with seconds. Python accepted both, so a
row stamped that way could never be found past its retention period.
"""

import sqlite3
import unittest

import grocery
from errors import GroceryError


class TimestampValidationTests(unittest.TestCase):
    def test_offsets_must_be_hours_and_minutes_with_a_colon(self):
        for value in ("2026-09-13T10:00:00+05:00:30", "2026-09-13T10:00:00-0500",
                      "2026-09-13T10:00:00+0530"):
            with self.subTest(value=value), self.assertRaises(GroceryError):
                grocery.validate_timestamp(value)

    def test_years_before_1900_are_refused(self):
        for value in ("1899-12-31T23:00:00-05:00", "0001-01-01T00:00:00+00:00"):
            with self.subTest(value=value), self.assertRaises(GroceryError):
                grocery.validate_timestamp(value)

    def test_accepted_stamps_are_readable_by_sqlite(self):
        conn = sqlite3.connect(":memory:")
        for value in ("2026-09-13T10:00:00-04:00", "2026-09-13T10:00:00+05:30",
                      "2026-09-13T10:00:00Z", "2026-09-13T10:00:00.123456-03:00",
                      "2026-09-13T10:00:00", "1900-01-01T00:00:00+00:00"):
            with self.subTest(value=value):
                stamped = grocery.validate_timestamp(value)
                self.assertIsNotNone(
                    conn.execute("SELECT unixepoch(?)", (stamped,)).fetchone()[0], stamped)
        conn.close()


if __name__ == "__main__":
    unittest.main()
