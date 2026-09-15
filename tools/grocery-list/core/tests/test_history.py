"""Who changed what, when, and in whose clock.

Covers the per-person timezone, the phone-safe name used whenever a person is
shown to someone else, who closed a trip, where a change came from, and the
member-facing `activity` feed built on the event log.
"""

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import cli
import db
import groups
import grocery
import people
import render

NY_PHONE = "+19175550182"
SP_PHONE = "+5511900000021"


class TimezoneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "t.sqlite3"
        self.conn = grocery.connect(self.path)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_people_gain_a_timezone_column(self):
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(people)")}
        self.assertIn("timezone", columns)

    def test_timezone_defaults_from_the_country_code(self):
        people.remember_person(self.conn, SP_PHONE, "pt", "Kim")
        people.remember_person(self.conn, NY_PHONE, "pt", "Jo")
        self.assertEqual(people.timezone_for(self.conn, SP_PHONE), "America/Sao_Paulo")
        self.assertEqual(people.timezone_for(self.conn, NY_PHONE), "America/New_York")

    def test_unknown_people_and_non_phones_get_the_household_default(self):
        self.assertEqual(people.timezone_for(self.conn, None), grocery.DEFAULT_TZ)
        self.assertEqual(people.timezone_for(self.conn, "owner"), grocery.DEFAULT_TZ)
        # Not on file, but the number alone is still a good guess.
        self.assertEqual(people.timezone_for(self.conn, "+5521900000000"),
                         "America/Sao_Paulo")

    def test_operator_override_wins_and_auto_restores_the_default(self):
        people.remember_person(self.conn, NY_PHONE, "pt", "Sam")
        people.set_timezone(self.conn, NY_PHONE, "Europe/Lisbon")
        self.assertEqual(people.timezone_for(self.conn, NY_PHONE), "Europe/Lisbon")
        people.set_timezone(self.conn, NY_PHONE, "auto")
        self.assertEqual(people.timezone_for(self.conn, NY_PHONE), "America/New_York")

    def test_override_rejects_an_invented_zone_and_an_unknown_person(self):
        people.remember_person(self.conn, NY_PHONE, "pt", "Sam")
        with self.assertRaises(grocery.GroceryError):
            people.set_timezone(self.conn, NY_PHONE, "Mars/Olympus_Mons")
        with self.assertRaises(grocery.GroceryError):
            people.set_timezone(self.conn, "+15550000000", "Europe/Lisbon")

    def test_roster_sync_does_not_wipe_an_override(self):
        people.remember_person(self.conn, NY_PHONE, "pt", "Sam")
        people.set_timezone(self.conn, NY_PHONE, "Europe/Lisbon")
        people.remember_person(self.conn, NY_PHONE, "en", "Sam Example")
        self.assertEqual(people.timezone_for(self.conn, NY_PHONE), "Europe/Lisbon")

    def test_override_is_an_operator_command(self):
        people.remember_person(self.conn, NY_PHONE, "pt", "Sam")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.run(["--db", str(self.path), "who", "timezone", NY_PHONE,
                     "America/Chicago"])
        self.assertEqual(json.loads(out.getvalue())["timezone"], "America/Chicago")
        self.assertEqual(people.timezone_for(self.conn, NY_PHONE), "America/Chicago")

    def test_a_bogus_stored_zone_falls_back_instead_of_crashing(self):
        """QA L4: set_timezone validates, but a row edited by hand does not."""
        people.remember_person(self.conn, SP_PHONE, "pt", "Kim")
        people.remember_person(self.conn, "owner", "pt", "Jo")
        self.conn.execute("UPDATE people SET timezone = 'Mars/Olympus_Mons'")
        self.conn.commit()
        self.assertEqual(people.timezone_for(self.conn, SP_PHONE), "America/Sao_Paulo")
        self.assertEqual(people.timezone_for(self.conn, "owner"), grocery.DEFAULT_TZ)
        import activity
        home = groups.group_row(self.conn, None, create=True)["id"]
        result = activity.report(self.conn, SP_PHONE, home, "pt")
        self.assertEqual(result["timezone"], "America/Sao_Paulo")

    def test_portugal_and_the_uk_have_their_own_zones(self):
        self.assertEqual(people.timezone_for(self.conn, "+351912345678"), "Europe/Lisbon")
        self.assertEqual(people.timezone_for(self.conn, "+447700900123"), "Europe/London")
        self.assertEqual(people.timezone_for(self.conn, "+12015550100"), "America/New_York")

    def test_migration_is_idempotent(self):
        self.conn.close()
        for _ in range(2):
            self.conn = grocery.connect(self.path)
            columns = [r["name"] for r in self.conn.execute("PRAGMA table_info(people)")]
            self.assertEqual(columns.count("timezone"), 1)
            self.conn.close()
        self.conn = grocery.connect(self.path)


class PublicLabelTests(unittest.TestCase):
    """A person shown to someone else is a name, never a phone number."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = grocery.connect(Path(self.temp.name) / "t.sqlite3")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_prefers_the_display_name(self):
        people.remember_person(self.conn, SP_PHONE, "pt", "Alex")
        self.assertEqual(people.public_label(self.conn, SP_PHONE), "Alex")

    def test_masks_a_phone_with_no_name(self):
        label = people.public_label(self.conn, SP_PHONE)
        self.assertNotIn("900000021", label)
        self.assertNotIn("+55", label)
        self.assertTrue(label.endswith("0021"))

    def test_blank_actor_has_no_label(self):
        self.assertEqual(people.public_label(self.conn, None), "")
        self.assertEqual(people.public_label(self.conn, ""), "")

    def test_list_footer_never_prints_a_nameless_phone(self):
        grocery.ingest_items(
            self.conn, "Costco", grocery.parse_items_json('["Milk"]'), "text",
            actor=SP_PHONE,
        )
        stamp = render.render_stamp(render.last_touched(self.conn, "Costco"), "pt")
        self.assertNotIn("900000021", stamp)
        self.assertIn("0021", stamp)


def run_cli(path, argv):
    """Run the CLI in-process and return its stdout."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.run(["--db", str(path), *argv])
    return out.getvalue()


class ClosedByTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "t.sqlite3"
        self.conn = grocery.connect(self.path)
        self.home = groups.group_row(self.conn, None, create=True)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def add(self, name, actor, group_id=None, store="Costco"):
        grocery.ingest_items(
            self.conn, store, grocery.parse_items_json(json.dumps([name])), "text",
            actor=actor, group_id=group_id or self.home["id"],
        )

    def forget_closers(self):
        """Put the database back in the shape it had before closed_by existed."""
        self.conn.execute("ALTER TABLE trips DROP COLUMN closed_by")
        # The previous engine never set a schema version.
        self.conn.execute("PRAGMA user_version = 0")
        self.conn.commit()
        self.conn.close()
        self.conn = grocery.connect(self.path)

    def closers(self):
        return [r["closed_by"] for r in
                self.conn.execute("SELECT closed_by FROM trips ORDER BY id")]

    def test_close_records_who_closed(self):
        self.add("Milk", NY_PHONE)
        grocery.close_trip(self.conn, "Costco", actor=SP_PHONE, group_id=self.home["id"])
        self.assertEqual(self.closers(), [SP_PHONE])

    def test_backfill_recovers_the_closer_from_the_log(self):
        self.add("Milk", NY_PHONE)
        grocery.close_trip(self.conn, "Costco", actor=SP_PHONE, group_id=self.home["id"])
        self.forget_closers()
        self.assertEqual(self.closers(), [SP_PHONE])

    def test_backfill_survives_a_trip_id_reused_after_reopen(self):
        """The live shape: close, reopen, close again within seconds.

        Reopen deletes the trip, and SQLite hands the next close the same id,
        so the log holds two sets of trip events under one trip_id. Only the
        set written at the trip's own closed_at belongs to it.
        """
        self.add("Milk", NY_PHONE)
        grocery.close_trip(self.conn, "Costco", "2026-08-30T19:22:37-04:00",
                           actor=NY_PHONE, group_id=self.home["id"])
        grocery.reopen_trip(self.conn, "Costco", actor=NY_PHONE, group_id=self.home["id"])
        second = grocery.close_trip(self.conn, "Costco", "2026-08-30T19:22:57-04:00",
                                    actor=SP_PHONE, group_id=self.home["id"])
        ids = {r["trip_id"] for r in self.conn.execute(
            "SELECT trip_id FROM events WHERE trip_id IS NOT NULL")}
        self.assertEqual(ids, {second["trip_id"]})  # the reuse this test is about
        self.forget_closers()
        self.assertEqual(self.closers(), [SP_PHONE])

    def test_backfill_does_not_credit_an_actorless_reclose(self):
        """QA L1: the reopened close's events share the reused trip id. Only the
        timestamp tells them apart, so a close with no actor must stay unknown
        rather than inherit the earlier closer."""
        self.add("Milk", NY_PHONE)
        grocery.close_trip(self.conn, "Costco", "2026-08-30T19:22:37-04:00",
                           actor=NY_PHONE, group_id=self.home["id"])
        grocery.reopen_trip(self.conn, "Costco", actor=NY_PHONE, group_id=self.home["id"])
        second = grocery.close_trip(self.conn, "Costco", "2026-08-30T19:22:57-04:00",
                                    actor=None, group_id=self.home["id"])
        reused = {r["trip_id"] for r in self.conn.execute(
            "SELECT trip_id FROM events WHERE actor = ?", (NY_PHONE,))
            if r["trip_id"] is not None}
        self.assertEqual(reused, {second["trip_id"]})
        self.forget_closers()
        self.assertEqual(self.closers(), [None])

    def test_backfill_reads_only_the_trips_own_household(self):
        self.add("Milk", NY_PHONE)
        trip = grocery.close_trip(self.conn, "Costco", "2026-08-30T19:00:00-04:00",
                                  actor=NY_PHONE, group_id=self.home["id"])
        other = groups.group_row(self.conn, "Neighbours", create=True)
        # A neighbour's event that collides on trip id and time, written later.
        db.record_event(
            self.conn, occurred_at=trip["closed_at"], store="Costco", name="Beer",
            action="trip_purchased", actor="sam", trip_id=trip["trip_id"],
            group_id=other["id"],
        )
        self.forget_closers()
        self.assertEqual(self.closers(), [NY_PHONE])

    def test_backfill_is_idempotent_and_keeps_what_is_there(self):
        self.add("Milk", NY_PHONE)
        grocery.close_trip(self.conn, "Costco", actor=SP_PHONE, group_id=self.home["id"])
        self.conn.execute("INSERT INTO trips(store_id, closed_at) VALUES (1, ?)",
                          ("2026-01-01T00:00:00-05:00",))  # no events: unknowable
        self.conn.commit()
        self.forget_closers()
        self.assertEqual(self.closers(), [SP_PHONE, None])
        self.conn.execute("UPDATE trips SET closed_by = 'operator' WHERE id = 1")
        self.conn.commit()
        self.conn.close()
        self.conn = grocery.connect(self.path)
        self.assertEqual(self.closers(), ["operator", None])

    def test_history_names_the_closer_without_their_number(self):
        groups.add_member(self.conn, None, NY_PHONE, "owner")
        groups.add_member(self.conn, None, SP_PHONE, "member")
        people.remember_person(self.conn, NY_PHONE, "pt", "Jo")
        self.add("Milk", NY_PHONE)
        grocery.close_trip(self.conn, "Costco", actor=SP_PHONE, group_id=self.home["id"])
        result = json.loads(run_cli(self.path, ["history", "--actor", NY_PHONE]))
        closer = result["trips"][0]["closed_by"]
        self.assertTrue(closer.endswith("0021"))
        self.assertNotIn("900000021", json.dumps(result))


class SourceDetailTests(unittest.TestCase):
    """buy, unbuy and remove say where they came from, the way add always has."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "t.sqlite3"
        self.conn = grocery.connect(self.path)
        grocery.ingest_items(
            self.conn, "Costco",
            grocery.parse_items_json('[{"name": "Milk", "note": "2%"}, "Bread"]'),
            "text", "wa:1", "milk 2% and bread", actor=NY_PHONE,
        )

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def last(self, action):
        return self.conn.execute(
            "SELECT * FROM events WHERE action = ? ORDER BY id DESC LIMIT 1", (action,)
        ).fetchone()

    def test_events_carry_the_raw_text(self):
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(events)")}
        self.assertIn("raw_text", columns)
        self.assertEqual(self.last("added")["raw_text"], "milk 2% and bread")

    def test_buy_unbuy_and_remove_record_their_source(self):
        source = dict(source_type="voice", source_ref="wa:voice-9",
                      raw_text="comprei o leite")
        grocery.set_status(self.conn, "Costco", ["Milk"], "purchased",
                           actor=SP_PHONE, **source)
        grocery.set_status(self.conn, "Costco", ["Milk"], "needed",
                           actor=SP_PHONE, **source)
        grocery.remove_items(self.conn, "Costco", ["Bread"], actor=SP_PHONE, **source)
        for action in ("purchased", "unpurchased", "removed"):
            with self.subTest(action=action):
                row = self.last(action)
                self.assertEqual((row["source_type"], row["source_ref"], row["raw_text"]),
                                 ("voice", "wa:voice-9", "comprei o leite"))
        # The item's own note is not overwritten by the message text.
        self.assertEqual(self.last("purchased")["note"], "2%")

    def test_buying_something_unlisted_keeps_its_source(self):
        grocery.set_status(self.conn, "Costco", ["Eggs"], "purchased", actor=SP_PHONE,
                           source_type="image", source_ref="wa:img-3", raw_text="receipt")
        for action in ("added", "purchased"):
            self.assertEqual(self.last(action)["source_type"], "image")
        sources = self.conn.execute(
            "SELECT s.source_type FROM item_sources s JOIN items i ON i.id = s.item_id "
            "WHERE i.name = 'Eggs'").fetchall()
        self.assertEqual([r["source_type"] for r in sources], ["image"])

    def test_cli_accepts_source_flags_and_defaults_to_none(self):
        run_cli(self.path, ["buy", "--store", "Costco", "Milk", "--source-type", "voice",
                            "--source-ref", "wa:2", "--raw-text", "got milk"])
        self.assertEqual(self.last("purchased")["source_type"], "voice")
        run_cli(self.path, ["remove", "--store", "Costco", "Bread"])
        self.assertEqual(self.last("removed")["source_type"], "")


if __name__ == "__main__":
    unittest.main()
