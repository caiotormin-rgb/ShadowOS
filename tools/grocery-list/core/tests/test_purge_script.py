"""scripts/purge_raw_text.py: the manual / cron purge, which reports counts only."""

import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import db
import grocery
import groups
import retention

APP = Path(__file__).resolve().parent.parent
SCRIPT = APP / "scripts" / "purge_raw_text.py"
CAIO = "+19175550182"
ALEX = "+5511900000078"
OLD_TEXT = "purge-script-old-transcript"
NEW_TEXT = "purge-script-new-message"


def ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()


def fixture(path: Path, version_one: bool = False) -> None:
    conn = db.connect(path, purge=False)
    home = groups.group_row(conn, None, create=True)["id"]
    groups.add_member(conn, None, CAIO, "owner")
    groups.add_member(conn, None, ALEX, "member")
    for days, text, actor in ((200, OLD_TEXT, CAIO), (120, OLD_TEXT, ALEX),
                              (10, NEW_TEXT, ALEX)):
        items = json.dumps([{"name": f"Leite {days}", "note": "~2"}, f"Pão {days}"])
        grocery.ingest_items(conn, "Costco", grocery.parse_items_json(items),
                             "voice", "wa:voice", text, ago(days), actor=actor, group_id=home)
    if version_one:
        for index in db.RETENTION_INDEXES:
            conn.execute(f"DROP INDEX {index}")
        conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()


class PurgeScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "grocery.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def run_script(self, *args, env=None):
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True, timeout=60,
                              env={**os.environ, **(env or {})})

    def files_text(self):
        return b"".join(p.read_bytes() for p in Path(self.temp.name).iterdir()
                        if p.name.startswith("grocery.sqlite3"))

    def assert_counts_only(self, output):
        for secret in (OLD_TEXT, NEW_TEXT, CAIO.lstrip("+"), ALEX.lstrip("+"),
                       CAIO[-4:], ALEX[-4:], "~2"):
            self.assertNotIn(secret, output)

    def summary(self, field):
        return tuple(field[k] for k in ("with_text_before", "due", "purged", "with_text_after"))

    def test_it_purges_reports_counts_and_then_has_nothing_to_do(self):
        fixture(self.path)
        result = self.run_script(str(self.path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_counts_only(result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["retention_days"], 90)
        self.assertFalse(report["dry_run"])
        self.assertEqual(report["wal_checkpoint"], {"complete": True})
        fields = report["fields"]
        self.assertEqual(list(fields), list(retention.FIELDS))
        self.assertEqual(self.summary(fields["events.raw_text"]), (6, 4, 4, 2))
        self.assertEqual(self.summary(fields["item_sources.raw_text"]), (6, 4, 4, 2))
        self.assertEqual(self.summary(fields["events.note"]), (3, 2, 2, 1))
        self.assertEqual(self.summary(fields["trip_items.note"]), (0, 0, 0, 0))
        self.assertEqual(report["table_rows"]["events"], 6)
        # Timestamps, not text; the survivors are the recent ones.
        events = fields["events.raw_text"]
        self.assertEqual(events["oldest_with_text_after"], events["newest_with_text_after"])
        self.assertLess(events["oldest_with_text_before"], events["oldest_with_text_after"])

        again = json.loads(self.run_script(str(self.path)).stdout)
        self.assertEqual(self.summary(again["fields"]["events.raw_text"]), (2, 0, 0, 2))

    def test_the_old_text_is_gone_from_the_files_not_just_the_rows(self):
        fixture(self.path)
        self.assertIn(OLD_TEXT.encode(), self.files_text())
        self.assertEqual(self.run_script(str(self.path)).returncode, 0)
        self.assertNotIn(OLD_TEXT.encode(), self.files_text())
        self.assertIn(NEW_TEXT.encode(), self.files_text())

    def test_dry_run_changes_nothing(self):
        fixture(self.path)
        conn = sqlite3.connect(self.path)
        before = conn.execute("SELECT id, raw_text, note FROM events ORDER BY id").fetchall()
        conn.close()
        result = self.run_script(str(self.path), "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_counts_only(result.stdout)
        report = json.loads(result.stdout)
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["fields"]["events.raw_text"]["due"], 4)
        self.assertEqual(report["fields"]["events.note"]["due"], 2)
        self.assertIsNone(report["fields"]["events.raw_text"]["purged"])
        conn = sqlite3.connect(self.path)
        self.assertEqual(conn.execute(
            "SELECT id, raw_text, note FROM events ORDER BY id").fetchall(), before)
        conn.close()

    def test_a_version_one_database_is_upgraded_then_purged(self):
        fixture(self.path, version_one=True)
        report = json.loads(self.run_script(str(self.path)).stdout)
        self.assertEqual(report["schema_version"], {"before": 1, "after": db.SCHEMA_VERSION})
        self.assertEqual(report["fields"]["events.raw_text"]["purged"], 4)

    def test_the_env_override_is_honoured_and_validated(self):
        fixture(self.path)
        report = json.loads(self.run_script(
            str(self.path), env={retention.ENV: "150"}).stdout)
        self.assertEqual((report["retention_days"], report["fields"]["events.raw_text"]["purged"]),
                         (150, 2))
        bad = self.run_script(str(self.path), env={retention.ENV: "0"})
        self.assertEqual(bad.returncode, 2)
        self.assertIn(retention.ENV, bad.stderr)
        self.assertEqual(bad.stdout, "")

    def test_it_will_not_create_a_database(self):
        missing = Path(self.temp.name) / "typo.sqlite3"
        result = self.run_script(str(missing))
        self.assertEqual(result.returncode, 2)
        self.assertFalse(missing.exists())


def load_script():
    spec = importlib.util.spec_from_file_location("purge_raw_text_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScriptSafetyTests(unittest.TestCase):
    """QA M1, L1, L3: who may run it, what it leaves behind, odd paths, and
    telling the operator when the WAL still holds old pages."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "grocery.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def run_script(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True, timeout=60)

    def sidecars(self, path=None):
        path = path or self.path
        return {suffix for suffix in ("-wal", "-shm")
                if Path(f"{path}{suffix}").exists()}

    def test_another_user_is_refused_before_anything_is_opened(self):
        fixture(self.path)
        self.assertEqual(self.sidecars(), set())
        script = load_script()
        stderr = io.StringIO()
        with mock.patch("os.geteuid", return_value=self.path.stat().st_uid + 1), \
                contextlib.redirect_stderr(stderr), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = script.main(["purge_raw_text.py", str(self.path)])
        self.assertEqual(code, 2)
        self.assertIn("owner", stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(self.sidecars(), set(), "it opened the database anyway")
        conn = sqlite3.connect(self.path)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM events WHERE raw_text = ?", (OLD_TEXT,)).fetchone()[0], 4)
        conn.close()

    def test_the_owner_is_let_through(self):
        fixture(self.path)
        script = load_script()
        with mock.patch("os.geteuid", return_value=self.path.stat().st_uid), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(script.main(["purge_raw_text.py", str(self.path), "--dry-run"]), 0)

    def test_a_dry_run_leaves_no_wal_or_shm_behind(self):
        fixture(self.path)
        self.assertEqual(self.sidecars(), set())
        result = self.run_script(str(self.path), "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["fields"]["events.raw_text"]["due"], 4)
        self.assertEqual(self.sidecars(), set())

    def test_a_dry_run_leaves_another_connections_wal_and_shm_alone(self):
        fixture(self.path)
        holder = sqlite3.connect(self.path)
        holder.execute("INSERT INTO people(actor, added_at) VALUES ('probe', '2026-09-13')")
        holder.commit()
        self.assertEqual(self.sidecars(), {"-wal", "-shm"})
        try:
            result = self.run_script(str(self.path), "--dry-run")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.sidecars(), {"-wal", "-shm"})
            # It read through the live WAL, not around it.
            report = json.loads(result.stdout)
            self.assertEqual(report["fields"]["events.raw_text"]["due"], 4)
            self.assertEqual(holder.execute(
                "SELECT COUNT(*) FROM people WHERE actor = 'probe'").fetchone()[0], 1)
        finally:
            holder.close()

    def test_paths_with_spaces_hashes_and_question_marks(self):
        for name in ("with space.sqlite3", "hash#name.sqlite3", "ques?tion.sqlite3",
                     "mode=rw&x.sqlite3"):
            with self.subTest(name=name):
                path = self.root / name
                fixture(path)
                dry = self.run_script(str(path), "--dry-run")
                self.assertEqual(dry.returncode, 0, dry.stderr)
                self.assertEqual(json.loads(dry.stdout)["fields"]["events.raw_text"]["due"], 4)
                self.assertEqual(self.sidecars(path), set())
                real = self.run_script(str(path))
                self.assertEqual(real.returncode, 0, real.stderr)
                self.assertEqual(json.loads(real.stdout)["fields"]["events.raw_text"]["purged"], 4)

    def test_a_directory_is_refused(self):
        result = self.run_script(str(self.root))
        self.assertEqual(result.returncode, 2)
        self.assertIn("not a file", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_an_incomplete_checkpoint_is_a_distinct_failure(self):
        """A reader holding an old snapshot keeps the pre-purge pages in the
        -wal; the operator must hear that, not see exit 0."""
        fixture(self.path)
        reader = sqlite3.connect(self.path, isolation_level=None)
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM events").fetchone()
        try:
            started = time.monotonic()
            result = self.run_script(str(self.path))
            elapsed = time.monotonic() - started
        finally:
            reader.execute("ROLLBACK")
            reader.close()
        self.assertEqual(result.returncode, 3, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["wal_checkpoint"], {"complete": False})
        self.assertEqual(report["fields"]["events.raw_text"]["purged"], 4)
        self.assertIn("checkpoint", result.stderr)
        self.assertLess(elapsed, 10, "it waited out the full busy timeout")


class ManyDatabasesTests(unittest.TestCase):
    """QA M3: the family list is not the only copy; each requester's private
    list is its own database, and so is any other path the operator names."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.private = self.root / "private"
        self.private.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def run_script(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True, timeout=120)

    def private_lists(self, count=3):
        stems = [f"{index:024x}"[-24:].replace("0", "a", 1) for index in range(count)]
        for stem in stems:
            fixture(self.private / f"{stem}.sqlite3")
        (self.private / "README.txt").write_text("not a database")
        (self.private / "nested").mkdir()
        fixture(self.private / "nested" / "deeper.sqlite3")      # not iterated
        return stems

    def test_several_paths_in_one_run(self):
        paths = [self.root / "a.sqlite3", self.root / "b.sqlite3"]
        for path in paths:
            fixture(path)
        result = self.run_script(*map(str, paths))
        self.assertEqual(result.returncode, 0, result.stderr)
        reports = json.loads(result.stdout)["databases"]
        self.assertEqual([r["database"] for r in reports], [str(p) for p in paths])
        self.assertEqual([r["fields"]["events.raw_text"]["purged"] for r in reports], [4, 4])

    def test_private_dir_purges_every_private_list_without_naming_it(self):
        stems = self.private_lists()
        result = self.run_script("--private-dir", str(self.private))
        self.assertEqual(result.returncode, 0, result.stderr)
        reports = json.loads(result.stdout)["databases"]
        self.assertEqual(len(reports), 3)
        self.assertEqual({r["fields"]["events.raw_text"]["purged"] for r in reports}, {4})
        self.assertEqual({r["fields"]["events.note"]["purged"] for r in reports}, {2})
        # A file name is a hash of the requester's phone: guessable, so unprinted.
        for stem in stems:
            self.assertNotIn(stem, result.stdout + result.stderr)
        self.assertEqual([r["database"] for r in reports],
                         [f"<private list {n}>" for n in (1, 2, 3)])
        self.assertNotIn(str(self.private), result.stdout)
        again = json.loads(self.run_script("--private-dir", str(self.private)).stdout)
        self.assertEqual({r["fields"]["events.raw_text"]["purged"] for r in again["databases"]}, {0})

    def test_paths_and_private_dir_together_and_dry_run(self):
        self.private_lists(2)
        family = self.root / "grocery.sqlite3"
        fixture(family)
        result = self.run_script(str(family), "--private-dir", str(self.private), "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        reports = json.loads(result.stdout)["databases"]
        self.assertEqual(len(reports), 3)
        self.assertEqual({r["fields"]["events.raw_text"]["due"] for r in reports}, {4})
        self.assertEqual({r["fields"]["events.raw_text"]["purged"] for r in reports}, {None})

    def test_one_bad_database_does_not_stop_the_rest(self):
        good = self.root / "good.sqlite3"
        fixture(good)
        missing = self.root / "missing.sqlite3"
        result = self.run_script(str(missing), str(good))
        self.assertEqual(result.returncode, 2)
        reports = json.loads(result.stdout)["databases"]
        self.assertIn("no database", reports[0]["error"])
        self.assertNotIn("fields", reports[0])
        self.assertEqual(reports[1]["fields"]["events.raw_text"]["purged"], 4)
        self.assertFalse(missing.exists())

    def test_a_missing_private_dir_is_refused(self):
        result = self.run_script("--private-dir", str(self.root / "nope"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("private", result.stderr)

    def test_garbage_and_empty_files_are_errors_and_the_good_list_is_still_purged(self):
        """QA N1: one unreadable file aborted the run with a traceback, and
        every list after it went unpurged on every cron run."""
        garbage, empty, good = "0" * 24, "1" * 24, "f" * 24      # sorted: bad ones first
        (self.private / f"{garbage}.sqlite3").write_bytes(b"not a database, just bytes " * 200)
        (self.private / f"{empty}.sqlite3").write_bytes(b"")
        fixture(self.private / f"{good}.sqlite3")
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                args = ["--private-dir", str(self.private)] + (["--dry-run"] if dry_run else [])
                result = self.run_script(*args)
                self.assertEqual(result.returncode, 4, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                reports = json.loads(result.stdout)["databases"]
                self.assertEqual(reports[0], {"database": "<private list 1>", "error": "DatabaseError"})
                self.assertEqual(reports[1], {"database": "<private list 2>", "error": "OperationalError"})
                field = reports[2]["fields"]["events.raw_text"]
                self.assertEqual(field["purged"], None if dry_run else 4)
                output = result.stdout + result.stderr
                for secret in (garbage, empty, good, str(self.private), "not a database",
                               "no such table", OLD_TEXT):
                    self.assertNotIn(secret, output)
        # The empty file was not turned into a fresh database.
        self.assertEqual((self.private / f"{empty}.sqlite3").stat().st_size, 0)

    def test_a_locked_database_times_out_and_the_next_is_still_purged(self):
        locked, good = self.root / "first.sqlite3", self.root / "good.sqlite3"
        fixture(locked)
        fixture(good)
        holder = sqlite3.connect(locked, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        try:
            started = time.monotonic()
            result = self.run_script(str(locked), str(good), "--busy-timeout", "1")
            elapsed = time.monotonic() - started
        finally:
            holder.execute("ROLLBACK")
            holder.close()
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertLess(elapsed, 10)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("database is locked", result.stdout + result.stderr)
        reports = json.loads(result.stdout)["databases"]
        self.assertEqual(reports[0], {"database": str(locked), "error": "OperationalError"})
        self.assertEqual(reports[1]["fields"]["events.raw_text"]["purged"], 4)
        conn = sqlite3.connect(locked)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM events WHERE raw_text = ?", (OLD_TEXT,)).fetchone()[0], 4)
        conn.close()

    def test_a_failure_outranks_an_incomplete_checkpoint(self):
        reading, broken = self.root / "reading.sqlite3", self.root / "broken.sqlite3"
        fixture(reading)
        broken.write_bytes(b"garbage " * 100)
        reader = sqlite3.connect(reading, isolation_level=None)
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM events").fetchone()
        try:
            result = self.run_script(str(reading), str(broken))
        finally:
            reader.execute("ROLLBACK")
            reader.close()
        self.assertEqual(result.returncode, 4, result.stderr)
        reports = json.loads(result.stdout)["databases"]
        self.assertEqual(reports[0]["wal_checkpoint"], {"complete": False})
        self.assertEqual(reports[1]["error"], "DatabaseError")

    def test_busy_timeout_must_be_a_sensible_number_of_seconds(self):
        fixture(self.root / "a.sqlite3")
        for value in ("0", "-1", "abc", "601", "nan"):
            with self.subTest(value=value):
                result = self.run_script(str(self.root / "a.sqlite3"), "--busy-timeout", value)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")

    def test_an_empty_private_dir_is_fine(self):
        result = self.run_script("--private-dir", str(self.private))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"databases": []})


if __name__ == "__main__":
    unittest.main()
