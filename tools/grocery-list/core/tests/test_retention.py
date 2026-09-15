"""Member-authored text is kept 90 days, then blanked; the history it explains stays.

The owner's rule (2026-09-13) is about the words: the transcript or message a
change came from, and any note derived from it that history copied. The change
itself is history and is never touched; the live list's own notes are kept.
"""

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import activity
import cli
import db
import grocery
import groups
import people
import retention
from errors import GroceryError

APP = Path(__file__).resolve().parent.parent
CAIO = "+19175550182"
ALEX = "+5511900000078"
NOW = datetime(2026, 9, 13, 20, 0, 0, tzinfo=ZoneInfo("America/New_York"))
OLD_TEXT = "transcript-marker-old"
NEW_TEXT = "transcript-marker-new"
ZERO = {field: 0 for field in retention.FIELDS}


def stamp(moment: datetime, zone: str = "America/New_York") -> str:
    return moment.astimezone(ZoneInfo(zone)).replace(microsecond=0).isoformat()


def counts(**by_field):
    """counts(events_raw_text=1) -> ZERO with "events.raw_text": 1."""
    names = {name.replace(".", "_"): name for name in retention.FIELDS}
    return {**ZERO, **{names[k]: v for k, v in by_field.items()}}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "t.sqlite3"
        self.conn = db.connect(self.path, purge=False)
        self.home = groups.group_row(self.conn, None, create=True)["id"]
        groups.add_member(self.conn, None, CAIO, "owner")
        groups.add_member(self.conn, None, ALEX, "member")
        people.remember_person(self.conn, CAIO, "pt", "Jo")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def ingest(self, name, at, text, note="", source_type="voice", ref="wa:voice-1"):
        grocery.ingest_items(
            self.conn, "Costco",
            grocery.parse_items_json(json.dumps([{"name": name, "note": note}])),
            source_type, ref, text, at, actor=CAIO, group_id=self.home)

    def event(self, name, at, text, action="purchased", note="", source_type="voice"):
        db.record_event(self.conn, occurred_at=at, store="Costco", name=name,
                        action=action, quantity=1.0, source_type=source_type,
                        source_ref="wa:voice-2", actor=ALEX, group_id=self.home,
                        raw_text=text, note=note)
        self.conn.commit()

    def column(self, table, column, key="id"):
        return {r[0]: r[1] for r in self.conn.execute(
            f"SELECT {key}, {column} FROM {table} ORDER BY id")}

    def everything_but_purged_text(self):
        """Every table and column except the fields retention may blank."""
        purged = {(f.table, f.column) for f in retention.FIELDS.values()}
        out = {}
        for table in [r[0] for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'")]:
            columns = [r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")
                       if (table, r[1]) not in purged]
            out[table] = self.conn.execute(
                f"SELECT {', '.join(columns)} FROM {table} ORDER BY rowid").fetchall()
        return out


class BoundaryTests(Fixture):
    def test_exactly_ninety_days_is_kept_and_one_second_more_is_not(self):
        edge = NOW - timedelta(days=90)
        self.event("Kept", stamp(edge), NEW_TEXT, note="~2")
        self.event("Gone", stamp(edge - timedelta(seconds=1)), OLD_TEXT, note="~2")
        purged = retention.purge(self.conn, now=NOW)
        self.assertEqual(purged, counts(events_raw_text=1, events_note=1))
        rows = {r["item_name"]: (r["raw_text"], r["note"]) for r in self.conn.execute(
            "SELECT item_name, raw_text, note FROM events")}
        self.assertEqual(rows, {"Kept": (NEW_TEXT, "~2"), "Gone": ("", "")})

    def test_the_boundary_is_an_instant_not_a_wall_clock(self):
        """The same instant written in Sao Paulo's offset, and one written a
        second earlier in UTC: offsets must not move the line."""
        edge = NOW - timedelta(days=90)
        self.event("SaoPaulo", stamp(edge, "America/Sao_Paulo"), NEW_TEXT)
        self.event("Utc", stamp(edge - timedelta(seconds=1), "UTC"), OLD_TEXT)
        self.event("Tokyo", stamp(edge + timedelta(seconds=1), "Asia/Tokyo"), NEW_TEXT)
        retention.purge(self.conn, now=NOW)
        rows = {r["item_name"]: r["raw_text"] for r in self.conn.execute(
            "SELECT item_name, raw_text FROM events")}
        self.assertEqual(rows, {"SaoPaulo": NEW_TEXT, "Utc": "", "Tokyo": NEW_TEXT})

    def test_item_sources_follow_the_same_rule(self):
        self.ingest("Leite", stamp(NOW - timedelta(days=91)), OLD_TEXT)
        self.ingest("Pão", stamp(NOW - timedelta(days=89)), NEW_TEXT)
        purged = retention.purge(self.conn, now=NOW)
        self.assertEqual(purged, counts(events_raw_text=1, item_sources_raw_text=1))
        self.assertEqual(sorted(self.column("item_sources", "raw_text").values()),
                         ["", NEW_TEXT])

    def test_the_env_override_moves_the_line(self):
        self.event("Month", stamp(NOW - timedelta(days=31)), OLD_TEXT)
        with mock.patch.dict("os.environ", {retention.ENV: "30"}):
            self.assertEqual(retention.purge(self.conn, now=NOW)["events.raw_text"], 1)

    def test_a_second_purge_finds_nothing(self):
        self.event("Gone", stamp(NOW - timedelta(days=200)), OLD_TEXT, note="pequeno")
        retention.purge(self.conn, now=NOW)
        before = self.conn.total_changes
        self.assertEqual(retention.purge(self.conn, now=NOW), ZERO)
        self.assertEqual(self.conn.total_changes, before)


class NoteTests(Fixture):
    """QA H1: the member's phrase also reached `note`, and history copies it."""

    OLD = NOW - timedelta(days=120)

    def test_old_event_notes_go_whatever_their_source(self):
        # An add from a message, and the copies later events make of the item's
        # note: a buy, a close, a reopen all log it with no source of their own.
        self.event("Leite", stamp(self.OLD), OLD_TEXT, action="added", note="a de coco")
        for action in ("purchased", "trip_purchased", "reopened", "removed"):
            self.event("Leite", stamp(self.OLD), "", action=action, note="a de coco",
                       source_type="")
        self.event("Leite", stamp(NOW - timedelta(days=1)), "", note="a de coco",
                   source_type="")
        purged = retention.purge(self.conn, now=NOW)
        self.assertEqual(purged, counts(events_raw_text=1, events_note=5))
        self.assertEqual(sorted(self.column("events", "note").values()),
                         ["", "", "", "", "", "a de coco"])

    def test_the_live_list_keeps_its_note(self):
        self.ingest("Leite", stamp(self.OLD), OLD_TEXT, note="a de coco")
        retention.purge(self.conn, now=NOW)
        self.assertEqual(self.column("events", "note", "action"), {"added": ""})
        self.assertEqual(self.column("items", "note", "name"), {"Leite": "a de coco"})

    def close(self, name, note, closed_at):
        self.ingest(name, stamp(closed_at - timedelta(hours=1)), "", note=note)
        grocery.set_status(self.conn, "Costco", [name], "purchased", actor=CAIO,
                           group_id=self.home)
        grocery.close_trip(self.conn, "Costco", stamp(closed_at), CAIO, self.home)

    def test_archived_trip_notes_go_once_the_trip_is_past_retention(self):
        self.close("Leite", "a de coco", self.OLD)
        self.close("Pão", "pequeno", NOW - timedelta(days=2))
        self.ingest("Ovos", stamp(self.OLD), "", note="~12")    # still on the list
        before = self.everything_but_purged_text()
        purged = retention.purge(self.conn, now=NOW)
        self.assertEqual(purged["trip_items.note"], 1)
        self.assertEqual(self.column("trip_items", "note", "name"),
                         {"Leite": "", "Pão": "pequeno"})
        self.assertEqual(self.column("items", "note", "name"), {"Ovos": "~12"})
        self.assertEqual(self.everything_but_purged_text(), before)

        # History still reads: both trips, their items and outcomes, no note.
        out = self.cli_json(["history", "--store", "Costco", "--actor", CAIO])
        trips = {t["items"][0]["name"]: t["items"][0] for t in out["trips"]}
        self.assertEqual((trips["Leite"]["outcome"], trips["Leite"]["note"]), ("purchased", ""))
        self.assertEqual(trips["Pão"]["note"], "pequeno")

    def cli_json(self, argv):
        import contextlib, io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            cli.run(["--db", str(self.path), *argv])
        return json.loads(buffer.getvalue())

    def test_the_trip_probe_and_every_other_probe_use_their_index(self):
        cutoff = retention.cutoff(NOW, 90)
        for name, field in retention.FIELDS.items():
            plan = " ".join(r[3] for r in self.conn.execute(
                f"EXPLAIN QUERY PLAN {retention.exists_sql(name)}",
                retention._params(cutoff)))
            self.assertIn(f"INDEX {field.index}", plan, name)


class HistoryIsKeptTests(Fixture):
    def scenario(self):
        old = NOW - timedelta(days=120)
        self.ingest("Leite", stamp(old), OLD_TEXT, note="2%")
        self.ingest("Leite", stamp(old + timedelta(hours=1)), OLD_TEXT, note="2%")
        self.event("Leite", stamp(old + timedelta(hours=2)), OLD_TEXT, note="2%")
        self.ingest("Pão", stamp(NOW - timedelta(days=3)), NEW_TEXT, note="pequeno")

    def test_every_other_column_and_every_row_is_untouched(self):
        self.scenario()
        before = self.everything_but_purged_text()
        purged = retention.purge(self.conn, now=NOW)
        self.assertEqual(purged, counts(events_raw_text=3, item_sources_raw_text=2,
                                        events_note=3))
        self.assertEqual(self.everything_but_purged_text(), before)
        self.assertEqual(self.column("items", "note", "name"), {"Leite": "2%", "Pão": "pequeno"})

    def test_recent_text_survives(self):
        self.scenario()
        retention.purge(self.conn, now=NOW)
        self.assertEqual([tuple(r) for r in self.conn.execute(
            "SELECT raw_text, note FROM events WHERE item_name = 'Pão'")],
            [(NEW_TEXT, "pequeno")])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM item_sources WHERE raw_text = ?", (NEW_TEXT,)).fetchone()[0], 1)

    def test_activity_reads_the_same_before_and_after(self):
        self.scenario()
        for lang in ("pt", "en"):
            before = activity.report(self.conn, CAIO, self.home, lang, now=stamp(NOW))
            retention.purge(self.conn, now=NOW)
            self.assertEqual(activity.report(self.conn, CAIO, self.home, lang, now=stamp(NOW)),
                             before)
            self.assertGreaterEqual(before["count"], 3)


class QuietConnectTests(Fixture):
    def test_connect_writes_nothing_when_no_text_is_old(self):
        self.ingest("Pão", stamp(datetime.now(timezone.utc) - timedelta(days=89)), NEW_TEXT,
                    note="pequeno")
        # Old history without text is not "due": it holds nothing to purge.
        self.ingest("Leite", "2025-01-01T09:00:00-05:00", "")
        self.conn.close()
        for _ in range(2):
            self.conn = db.connect(self.path)
            self.assertEqual(self.conn.total_changes, 0)
            self.assertFalse(self.conn.in_transaction)
            self.conn.close()
        self.conn = db.connect(self.path, purge=False)
        self.assertEqual(sorted(self.column("events", "raw_text").values()), ["", NEW_TEXT])

    def test_connect_purges_what_is_due_then_goes_quiet(self):
        self.ingest("Leite", "2025-01-01T09:00:00-05:00", OLD_TEXT)
        self.conn.close()
        self.conn = db.connect(self.path)
        self.assertEqual(self.conn.total_changes, 2)
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        # Restored to whatever this SQLite build defaults to (Debian's is ON).
        default = sqlite3.connect(":memory:").execute("PRAGMA secure_delete").fetchone()[0]
        self.assertEqual(self.conn.execute("PRAGMA secure_delete").fetchone()[0], default)
        self.assertFalse(self.conn.in_transaction)
        self.conn.close()
        self.conn = db.connect(self.path)
        self.assertEqual(self.conn.total_changes, 0)
        self.assertEqual(set(self.column("events", "raw_text").values()), {""})

    def test_a_locked_database_skips_the_purge_instead_of_failing_the_request(self):
        self.ingest("Leite", "2025-01-01T09:00:00-05:00", OLD_TEXT)
        holder = sqlite3.connect(self.path, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        try:
            conn = sqlite3.connect(self.path, timeout=0.1)
            retention.purge_expired(conn)          # no exception
            conn.close()
        finally:
            holder.execute("ROLLBACK")
            holder.close()
        self.assertIn(OLD_TEXT, self.column("events", "raw_text").values())


class LockAndBytesTests(Fixture):
    def test_a_held_write_lock_costs_a_read_about_a_second_not_thirty(self):
        """QA H2: with text due and another process holding the write lock,
        the purge waited out connect()'s 30 s timeout — past the plugin's 15 s."""
        self.ingest("Leite", "2025-01-01T09:00:00-05:00", OLD_TEXT)
        self.conn.close()
        holder = sqlite3.connect(self.path, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        try:
            started = time.monotonic()
            result = subprocess.run(
                [sys.executable, str(APP / "grocery.py"), "--db", str(self.path),
                 "list", "--store", "Costco", "--actor", CAIO],
                capture_output=True, text=True, timeout=60)
            elapsed = time.monotonic() - started
        finally:
            holder.execute("ROLLBACK")
            holder.close()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(elapsed, 2.5)

        self.conn = db.connect(self.path, purge=False)
        self.assertIn(OLD_TEXT, self.column("events", "raw_text").values(),
                      "the busy open should have skipped the purge")
        self.conn.close()
        self.conn = db.connect(self.path)                   # the next open purges
        self.assertEqual(set(self.column("events", "raw_text").values()), {""})
        self.assertEqual(self.conn.execute("PRAGMA busy_timeout").fetchone()[0], 30000)

    def test_old_text_is_zeroed_even_where_secure_delete_was_off(self):
        """QA L4: the purge must turn secure_delete on itself, not rely on the
        build default (Debian's is ON, which hid a missing pragma).

        The text is long, with the marker at its start: SQLite writes the
        shorter replacement row into the tail of the freed space, which is where
        a short raw_text sat — so a short marker vanished even with the pragma
        removed, and this test could not tell."""
        long_text = OLD_TEXT + " " + "filler " * 200
        self.ingest("Leite", "2025-01-01T09:00:00-05:00", long_text)
        self.conn.execute("PRAGMA secure_delete = OFF")
        self.assertEqual(self.conn.execute("PRAGMA secure_delete").fetchone()[0], 0)
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.assertIn(OLD_TEXT.encode(), self.path.read_bytes())

        self.assertEqual(retention.purge(self.conn, now=NOW)["events.raw_text"], 1)
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.assertNotIn(OLD_TEXT.encode(), self.path.read_bytes())
        self.assertEqual(self.conn.execute("PRAGMA secure_delete").fetchone()[0], 0)


class EnvironmentTests(unittest.TestCase):
    def days(self, value):
        return retention.retention_days({retention.ENV: value})

    def test_default_is_ninety(self):
        self.assertEqual(retention.DEFAULT_DAYS, 90)
        self.assertEqual(retention.retention_days({}), 90)
        self.assertEqual(self.days(""), 90)
        self.assertEqual(self.days("  "), 90)

    def test_whole_days_in_range_are_accepted(self):
        for value, expected in (("1", 1), ("30", 30), (" 365 ", 365), ("3650", 3650)):
            self.assertEqual(self.days(value), expected)

    def test_anything_else_is_refused(self):
        for value in ("0", "3651", "-5", "1.5", "90d", "abc", "1e3", "99999", "+30", "٣"):
            with self.subTest(value=value), self.assertRaises(GroceryError):
                self.days(value)

    def test_a_bad_value_stops_the_command_with_a_clear_error(self):
        with tempfile.TemporaryDirectory() as root:
            env = {**__import__("os").environ, retention.ENV: "ninety"}
            result = subprocess.run(
                [sys.executable, str(APP / "grocery.py"), "--db", str(Path(root) / "t.sqlite3"),
                 "stores", "--actor", CAIO],
                capture_output=True, text=True, timeout=60, env=env)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn(retention.ENV, result.stderr)
            self.assertNotIn("Traceback", result.stderr)


class SkillWordingTests(unittest.TestCase):
    """QA H1: the agent was told to put the member's raw phrase in `note`."""

    SKILLS = (
        APP.parent / "skill" / "SKILL.md",
        APP / "docs" / "SKILL.draft.md",
    )

    def test_no_skill_sends_the_members_words_to_note(self):
        for path in self.SKILLS:
            text = " ".join(path.read_text(encoding="utf-8").split())
            with self.subTest(skill=path.name):
                self.assertNotIn("raw phrase in", text)
                self.assertIn("never their sentence", text)


WORKER = r"""
import contextlib, io, sys, time
sys.path.insert(0, sys.argv[1])
import cli
start = float(sys.argv[2])
while time.time() < start:
    time.sleep(0.001)
with contextlib.redirect_stdout(io.StringIO()):
    cli.run(sys.argv[3:])
"""


class ConcurrentPurgeTests(unittest.TestCase):
    """Every message is its own process; the first ones after text turns 90 days
    old all find it due at once."""

    ROUNDS = 10
    JOBS = [
        ["list", "--store", "Costco", "--actor", CAIO],
        ["activity", "--actor", ALEX],
        ["ingest", "--store", "Costco", "--source-type", "voice", "--raw-text", NEW_TEXT,
         "--items-json", '["Ovos"]', "--actor", ALEX],
        ["history", "--store", "Costco", "--actor", CAIO],
        ["buy", "--store", "Costco", "Leite", "--actor", CAIO, "--raw-text", NEW_TEXT],
        ["stores", "--actor", ALEX],
    ]

    def fixture(self, path):
        conn = db.connect(path, purge=False)
        home = groups.group_row(conn, None, create=True)["id"]
        groups.add_member(conn, None, CAIO, "owner")
        groups.add_member(conn, None, ALEX, "member")
        for day in range(95, 130):
            grocery.ingest_items(
                conn, "Costco", grocery.parse_items_json(f'["Leite", "Item {day}"]'), "voice",
                "wa:v", OLD_TEXT,
                stamp(datetime.now(timezone.utc) - timedelta(days=day)),
                actor=CAIO, group_id=home)
        conn.close()

    def test_racing_opens_purge_once_and_none_fail(self):
        failures, wrong = [], []
        with tempfile.TemporaryDirectory() as root:
            fixture = Path(root) / "fixture.sqlite3"
            self.fixture(fixture)
            for round_ in range(self.ROUNDS):
                target = Path(root) / f"round{round_}.sqlite3"
                shutil.copy(fixture, target)
                start = time.time() + 0.8
                procs = [subprocess.Popen(
                    [sys.executable, "-c", WORKER, str(APP), str(start), "--db", str(target),
                     *argv], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    for argv in self.JOBS]
                for proc in procs:
                    _, err = proc.communicate(timeout=60)
                    if proc.returncode:
                        failures.append((err.strip().splitlines() or ["?"])[-1])
                conn = sqlite3.connect(target)
                left = sum(conn.execute(
                    f"SELECT COUNT(*) FROM {t} WHERE raw_text = ?", (OLD_TEXT,)).fetchone()[0]
                    for t in ("events", "item_sources"))
                kept = conn.execute(
                    "SELECT COUNT(*) FROM events WHERE raw_text = ?", (NEW_TEXT,)).fetchone()[0]
                events = conn.execute(
                    "SELECT COUNT(*) FROM events WHERE action IN ('added', 'merged')"
                ).fetchone()[0]
                conn.close()
                if left or kept != 2 or events != 71:
                    wrong.append((round_, left, kept, events))
        self.assertEqual(failures, [], f"{len(failures)} of "
                         f"{self.ROUNDS * len(self.JOBS)} concurrent requests failed")
        self.assertEqual(wrong, [], "old text left behind, new text lost, or history changed")


if __name__ == "__main__":
    unittest.main()
