import sqlite3
import unittest

from calctx.store import migrate
from tests.fixtures import CAL_ID, NOW, memory_conn

# Words that would indicate someone added description/body/attachment storage.
FORBIDDEN_COLUMN_TOKENS = ("description", "body", "payload", "content", "raw", "html",
                           "blob", "attachment", "conference", "hangout", "entrypoint",
                           "notes", "agenda")
ALLOWED_EXACT = {"cal_search"}  # FTS internals legitimately contain 'content='

# The index is private but is not a secret store. A syncToken is a list cursor,
# not a credential: on its own it authorizes nothing.
CREDENTIAL_TOKENS = ("access_token", "refresh_token", "client_secret", "password",
                     "bearer", "api_key")


class SchemaTest(unittest.TestCase):
    def setUp(self):
        self.conn = memory_conn()
        migrate(self.conn)
        self.conn.execute(
            "INSERT INTO cal_calendars (calendar_id, summary, time_zone, selected, synced_at) "
            "VALUES (?, 'Personal', 'UTC', 1, ?)", (CAL_ID, NOW))

    def _event(self, **over):
        cols = {"calendar_id": CAL_ID, "event_id": "e1", "kind": "single",
                "status": "confirmed", "start_kind": "timed", "start_utc": NOW,
                "start_tz": "UTC", "start_date": None, "end_utc": NOW + 3600,
                "end_tz": "UTC", "end_date": None, "synced_at": NOW}
        cols.update(over)
        names = ", ".join(cols)
        marks = ", ".join(f":{k}" for k in cols)
        self.conn.execute(f"INSERT INTO cal_events ({names}) VALUES ({marks})", cols)

    # -- migration -------------------------------------------------------
    def test_migrate_is_idempotent(self):
        migrate(self.conn)
        migrate(self.conn)
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_refuses_newer_schema(self):
        self.conn.execute("PRAGMA user_version = 99")
        with self.assertRaises(RuntimeError):
            migrate(self.conn)

    # -- what must not be stored -----------------------------------------
    def test_no_description_or_attachment_columns(self):
        """The no-descriptions rule is enforced against the live schema."""
        offenders = []
        tables = [r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            if table in ALLOWED_EXACT or table.startswith("cal_search"):
                continue
            for col in self.conn.execute(f"PRAGMA table_info({table})"):
                name = col[1].lower()
                for tok in FORBIDDEN_COLUMN_TOKENS:
                    if tok in name:
                        offenders.append(f"{table}.{col[1]}")
        self.assertEqual(offenders, [], f"description/body-like columns found: {offenders}")

    def test_no_credential_columns(self):
        offenders = []
        for table in [r[0] for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]:
            for col in self.conn.execute(f"PRAGMA table_info({table})"):
                name = col[1].lower()
                offenders += [f"{table}.{col[1]}" for t in CREDENTIAL_TOKENS if t in name]
        self.assertEqual(offenders, [], f"credential-like columns found: {offenders}")

    def test_there_is_no_outbox_or_draft_table(self):
        """mail-context has draft_links because it writes. This must not."""
        tables = {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for forbidden in ("cal_drafts", "cal_outbox", "cal_pending_writes", "cal_rsvp_queue"):
            self.assertNotIn(forbidden, tables)

    # -- the all-day / timed split ---------------------------------------
    def test_timed_event_requires_an_instant_and_a_zone(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._event(start_tz=None)          # instant without its zone
        with self.assertRaises(sqlite3.IntegrityError):
            self._event(start_utc=None)         # zone without its instant

    def test_all_day_event_may_not_carry_an_instant(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._event(event_id="a1", start_kind="all_day", start_date="2026-08-23",
                        start_utc=NOW, start_tz="UTC", end_utc=None, end_tz=None,
                        end_date="2026-08-23")

    def test_a_row_may_not_be_both_shapes(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._event(event_id="b1", start_date="2026-08-23")   # timed + a date

    def test_a_row_may_not_be_neither_shape(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._event(event_id="c1", start_utc=None, start_tz=None, start_date=None)

    def test_all_day_row_is_accepted_without_any_epoch(self):
        self._event(event_id="ok1", start_kind="all_day", start_date="2026-08-23",
                    start_utc=None, start_tz=None, end_utc=None, end_tz=None,
                    end_date="2026-08-23")
        row = self.conn.execute(
            "SELECT start_utc, start_date FROM cal_events WHERE event_id='ok1'").fetchone()
        self.assertIsNone(row["start_utc"], "a birthday is not an instant")
        self.assertEqual(row["start_date"], "2026-08-23")

    def test_instance_start_kind_is_constrained_too(self):
        self._event()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO cal_instances (calendar_id, instance_id, series_event_id,
                       start_kind, start_utc, start_tz, start_date, end_utc, end_tz, end_date,
                       order_ts, synced_at)
                   VALUES (?, 'i1', 'e1', 'all_day', ?, 'UTC', NULL, ?, 'UTC', NULL, ?, ?)""",
                (CAL_ID, NOW, NOW + 3600, NOW, NOW))

    def test_event_status_is_constrained(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._event(event_id="s1", status="rescheduled")

    def test_event_kind_is_constrained(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._event(event_id="k1", kind="draft")

    def test_run_kind_is_constrained(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO cal_sync_runs (kind, started_at) VALUES ('write', ?)", (NOW,))


if __name__ == "__main__":
    unittest.main()
