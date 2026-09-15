"""Opening the database: migrations are safe under concurrency and cheap after.

The plugin runs one engine process per WhatsApp message, so the first requests
after a deploy race each other through the schema upgrade. A check-then-ALTER
without a lock made some of them fail with "duplicate column name".
"""

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import db
import groups
import grocery
import people

APP = Path(__file__).resolve().parent.parent
CAIO = "+19175550182"
ALEX = "+5511900000078"

# Columns this branch adds to a database the previous engine created.
ADDED = (("people", "timezone"), ("trips", "closed_by"),
         ("events", "raw_text"), ("events", "previous_quantity"),
         ("items", "product_url"), ("trip_items", "product_url"),
         ("events", "product_url"))

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


def pre_migration_fixture(path: Path) -> None:
    """A populated database in the shape the previous engine left it."""
    conn = grocery.connect(path)
    home = groups.group_row(conn, None, create=True)["id"]
    groups.add_member(conn, None, CAIO, "owner")
    groups.add_member(conn, None, ALEX, "member")
    people.remember_person(conn, CAIO, "pt", "Jo")
    grocery.ingest_items(conn, "Costco", grocery.parse_items_json('["Leite", "Pão"]'),
                         "text", actor=CAIO, group_id=home)
    grocery.set_status(conn, "Costco", ["Pão"], "purchased", actor=ALEX, group_id=home)
    grocery.close_trip(conn, "Costco", actor=CAIO, group_id=home)
    # The previous engine had no retention indexes, and one names events.raw_text.
    for index in db.RETENTION_INDEXES:
        conn.execute(f"DROP INDEX {index}")
    for table, column in ADDED:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()


def version_one_fixture(path: Path) -> None:
    """A populated database exactly as SCHEMA_VERSION 1 left it: every column,
    no retention indexes, and message text both old and recent."""
    conn = grocery.connect(path)
    home = groups.group_row(conn, None, create=True)["id"]
    groups.add_member(conn, None, CAIO, "owner")
    grocery.ingest_items(conn, "Costco", grocery.parse_items_json('["Leite"]'), "voice",
                         "wa:voice-1", "old transcript", "2026-01-02T09:00:00-05:00",
                         actor=CAIO, group_id=home)
    grocery.ingest_items(conn, "Costco", grocery.parse_items_json('["Pão"]'), "text",
                         "", "recent message", actor=CAIO, group_id=home)
    for index in db.RETENTION_INDEXES:
        conn.execute(f"DROP INDEX {index}")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()


class ConcurrentMigrationTests(unittest.TestCase):
    ROUNDS = 15
    PROCS = 6

    def test_simultaneous_first_opens_all_succeed(self):
        commands = [
            ["activity", "--actor", CAIO],
            ["list", "--actor", ALEX, "--store", "Costco"],
            ["history", "--actor", CAIO, "--store", "Costco"],
            ["activity", "--actor", ALEX, "--since", "7d"],
        ]
        failures = []
        with tempfile.TemporaryDirectory() as root:
            fixture = Path(root) / "fixture.sqlite3"
            pre_migration_fixture(fixture)
            for round_ in range(self.ROUNDS):
                target = Path(root) / f"round{round_}.sqlite3"
                shutil.copy(fixture, target)
                start = time.time() + 0.8     # past interpreter start-up and imports
                procs = [
                    subprocess.Popen(
                        [sys.executable, "-c", WORKER, str(APP), str(start),
                         "--db", str(target), *commands[k % len(commands)]],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    for k in range(self.PROCS)
                ]
                for proc in procs:
                    _, err = proc.communicate(timeout=60)
                    if proc.returncode:
                        failures.append((err.strip().splitlines() or ["?"])[-1])
                conn = grocery.connect(target)
                for table, column in ADDED:
                    names = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
                    self.assertEqual(names.count(column), 1, f"{table}.{column}")
                conn.close()
        self.assertEqual(failures, [], f"{len(failures)} of "
                         f"{self.ROUNDS * self.PROCS} concurrent opens failed")


def schema_of(conn) -> dict[str, frozenset]:
    """Tables -> their columns as (name, type, notnull, default, pk), plus indexes.

    Sets, not lists: a column dropped and re-added moves to the end of the
    table, which changes nothing a query can see.
    """
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    shape = {t: frozenset(tuple(r)[1:] for r in conn.execute(f"PRAGMA table_info({t})"))
             for t in tables}
    shape["<indexes>"] = frozenset(index_definitions(conn).items())
    return shape


def index_definitions(conn) -> dict[str, str]:
    """Every explicit index: name -> its SQL, whitespace collapsed.

    Names and definitions both: an index redefined under the same name is a
    schema change too. sqlite_autoindex_* carry no SQL — they follow from a
    UNIQUE or PRIMARY KEY, which the column comparison already covers.
    """
    return {name: " ".join(sql.split()) for name, sql in conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")}


def migration_added_columns() -> set[tuple[str, str]]:
    """Columns a fresh database has only because a migration step added them."""
    base = sqlite3.connect(":memory:")
    for statement in db._statements(db.SCHEMA):
        base.execute(statement)
    created = {(t, c) for t in (r[0] for r in base.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"))
        for c in (r[1] for r in base.execute(f"PRAGMA table_info({t})"))}
    base.close()
    with tempfile.TemporaryDirectory() as root:
        conn = grocery.connect(Path(root) / "fresh.sqlite3")
        full = {(t, c) for t in (r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"))
            for c in (r[1] for r in conn.execute(f"PRAGMA table_info({t})"))}
        conn.close()
    return full - created


# What migration adds on top of db.SCHEMA, per SCHEMA_VERSION. When a step adds
# a column, bump db.SCHEMA_VERSION and add an entry here — the test below fails
# until both are done, because a step shipped without the bump never runs on an
# existing database.
MIGRATED_COLUMNS = {
    1: frozenset({
        ("stores", "group_id"), ("stores", "layout"),
        ("items", "canonical_name"),
        ("people", "default_store"), ("people", "timezone"),
        ("events", "group_id"), ("events", "raw_text"), ("events", "previous_quantity"),
        ("trips", "closed_by"),
    }),
}
# 2 added indexes only (db.RETENTION_INDEXES), so its columns are 1's. Indexes
# are pinned by VersionTwoIndexTests below, which the column check cannot see.
MIGRATED_COLUMNS[2] = MIGRATED_COLUMNS[1]
MIGRATED_COLUMNS[3] = MIGRATED_COLUMNS[2] | frozenset({
    ("items", "product_url"), ("trip_items", "product_url"),
    ("events", "product_url"),
})
MIGRATED_COLUMNS[4] = MIGRATED_COLUMNS[3]

# Every explicit index a fresh database has, per SCHEMA_VERSION (QA M2: the
# column check alone let an index-only step ship without a bump). A released
# version's entry never changes; a new index means a new version and entry.
MIGRATED_INDEXES = {
    1: {
        "events_action": "CREATE INDEX events_action ON events(action, occurred_at)",
        "events_item": "CREATE INDEX events_item ON events(normalized_name, occurred_at)",
        "events_store": "CREATE INDEX events_store ON events(store, occurred_at)",
        "items_canonical": "CREATE INDEX items_canonical ON items(store_id, canonical_name, unit)",
    },
}
MIGRATED_INDEXES[2] = {
    **MIGRATED_INDEXES[1],
    "events_raw_text_age":
        "CREATE INDEX events_raw_text_age ON events(occurred_at) WHERE raw_text != ''",
    "item_sources_raw_text_age":
        "CREATE INDEX item_sources_raw_text_age ON item_sources(observed_at) WHERE raw_text != ''",
    "events_note_age":
        "CREATE INDEX events_note_age ON events(occurred_at) WHERE note != ''",
    "trip_items_note_trip":
        "CREATE INDEX trip_items_note_trip ON trip_items(trip_id) WHERE note != ''",
}
MIGRATED_INDEXES[3] = MIGRATED_INDEXES[2]
MIGRATED_INDEXES[4] = MIGRATED_INDEXES[3]


class SchemaCompletenessTests(unittest.TestCase):
    """QA N2: the version gate must not hide a migration step."""

    def test_the_version_matches_the_migrated_schema(self):
        self.assertIn(db.SCHEMA_VERSION, MIGRATED_COLUMNS,
                      "SCHEMA_VERSION changed; record its schema in MIGRATED_COLUMNS")
        self.assertEqual(
            migration_added_columns(), set(MIGRATED_COLUMNS[db.SCHEMA_VERSION]),
            "migration now adds different columns, but SCHEMA_VERSION was not "
            "bumped: existing databases at this version would never get them")

    def test_the_version_matches_the_indexes_too(self):
        """QA M2: an index-only step changed nothing the column check could see."""
        self.assertIn(db.SCHEMA_VERSION, MIGRATED_INDEXES,
                      "SCHEMA_VERSION changed; record its indexes in MIGRATED_INDEXES")
        with tempfile.TemporaryDirectory() as root:
            conn = grocery.connect(Path(root) / "fresh.sqlite3")
            actual = index_definitions(conn)
            conn.close()
        self.assertEqual(
            actual, MIGRATED_INDEXES[db.SCHEMA_VERSION],
            "migration now creates different indexes, but SCHEMA_VERSION was not "
            "bumped: existing databases at this version would never get them")

    def test_re_running_from_version_zero_rebuilds_a_fresh_schema(self):
        """Strip every migration-added column that SQLite will drop, reset the
        version, reopen: the result must be indistinguishable from fresh."""
        with tempfile.TemporaryDirectory() as root:
            fresh = grocery.connect(Path(root) / "fresh.sqlite3")
            expected = schema_of(fresh)
            fresh.close()

            path = Path(root) / "stripped.sqlite3"
            pre_migration_fixture(path)
            conn = grocery.connect(path)
            dropped = []
            for table, column in sorted(migration_added_columns()):
                for (index,) in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'index' "
                        "AND tbl_name = ? AND sql LIKE ?", (table, f"%{column}%")).fetchall():
                    conn.execute(f"DROP INDEX {index}")
                try:
                    conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
                    dropped.append(f"{table}.{column}")
                except sqlite3.OperationalError:
                    pass      # part of a constraint (stores.group_id); cannot be dropped
            conn.execute("PRAGMA user_version = 0")
            conn.commit()
            conn.close()
            self.assertGreaterEqual(len(dropped), 7, dropped)

            conn = grocery.connect(path)
            self.assertEqual(schema_of(conn), expected)
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0],
                             db.SCHEMA_VERSION)
            conn.close()


class _StaleSchema:
    """A connection whose first look at a table misses a column that another
    process has just added — the window between the check and the ALTER."""

    def __init__(self, conn):
        self.conn = conn
        self.stale = True

    def execute(self, sql, *args):
        if self.stale and sql.startswith("PRAGMA table_info"):
            self.stale = False
            return iter(())
        return self.conn.execute(sql, *args)


class SchemaVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "t.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def test_a_column_added_by_someone_else_is_not_an_error(self):
        conn = grocery.connect(self.path)
        db.ensure_column(_StaleSchema(conn), "people", "timezone",
                         "timezone TEXT NOT NULL DEFAULT ''")
        conn.close()

    def test_opening_records_the_schema_version(self):
        conn = grocery.connect(self.path)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0],
                         db.SCHEMA_VERSION)
        conn.close()

    def test_an_old_database_is_upgraded_once(self):
        pre_migration_fixture(self.path)
        conn = grocery.connect(self.path)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0],
                         db.SCHEMA_VERSION)
        for table, column in ADDED:
            names = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
            self.assertIn(column, names)
        self.assertEqual(conn.execute("SELECT closed_by FROM trips").fetchone()[0], CAIO)
        # Foreign keys are back on for the request that follows the upgrade.
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        conn.close()

    def test_upgrade_rekeys_and_merges_existing_sourdough_duplicates(self):
        conn = grocery.connect(self.path)
        grocery.ingest_items(
            conn, "Costco", grocery.parse_items_json('[{"name":"sourdough bread"}]'),
            "text", source_ref="english",
        )
        first = conn.execute("SELECT * FROM items").fetchone()
        conn.execute(
            "UPDATE items SET status = 'purchased', canonical_name = 'sourdough bread' "
            "WHERE id = ?",
            (first["id"],),
        )
        second = conn.execute(
            """
            INSERT INTO items(
                store_id, name, normalized_name, canonical_name, quantity,
                unit, note, product_url, status, first_added_at, updated_at
            ) VALUES (?, 'Pao sourdough', 'pao sourdough', 'pao sourdough', 2,
                      '', 'seeded', '', 'needed',
                      '2026-09-14T12:00:00-04:00', '2026-09-14T12:00:00-04:00')
            """,
            (first["store_id"],),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO item_sources(item_id, source_type, source_ref, raw_text, observed_at)
            VALUES (?, 'text', 'portuguese', '', '2026-09-14T12:00:00-04:00')
            """,
            (second,),
        )
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
        conn.close()

        conn = grocery.connect(self.path)
        rows = conn.execute("SELECT * FROM items").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Sourdough bread")
        self.assertEqual(rows[0]["canonical_name"], "sourdough bread")
        self.assertEqual(rows[0]["quantity"], 2)
        self.assertEqual(rows[0]["note"], "seeded")
        self.assertEqual(rows[0]["status"], "needed")
        sources = conn.execute(
            "SELECT item_id, source_ref FROM item_sources ORDER BY source_ref"
        ).fetchall()
        self.assertEqual([r["source_ref"] for r in sources], ["english", "portuguese"])
        self.assertEqual({r["item_id"] for r in sources}, {rows[0]["id"]})
        conn.close()


class DeployHelperTests(unittest.TestCase):
    """scripts/: run the upgrade right after a merge, and undo H2 before a rollback."""

    SCRIPTS = APP / "scripts"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "grocery.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def pre_migrate(self, path):
        return subprocess.run([sys.executable, str(self.SCRIPTS / "pre_migrate.py"), str(path)],
                              capture_output=True, text=True, timeout=60)

    def test_pre_migrate_reports_the_upgrade_then_nothing_to_do(self):
        pre_migration_fixture(self.path)
        first = self.pre_migrate(self.path)
        self.assertEqual(first.returncode, 0, first.stderr)
        report = json.loads(first.stdout)
        self.assertEqual(report["schema_version"],
                         {"before": 0, "after": db.SCHEMA_VERSION, "target": db.SCHEMA_VERSION})
        self.assertTrue(report["migrated"])
        self.assertEqual(set(report["columns_added"]),
                         {f"{t}.{c}" for t, c in ADDED})
        self.assertEqual(report["trips"], {"total": 1, "with_closer": 1})

        second = json.loads(self.pre_migrate(self.path).stdout)
        self.assertFalse(second["migrated"])
        self.assertEqual(second["columns_added"], [])

    def test_pre_migrate_will_not_create_a_database(self):
        missing = Path(self.temp.name) / "typo.sqlite3"
        result = self.pre_migrate(missing)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(missing.exists())

    def rollback(self, *flags, path=None):
        return subprocess.run(
            [sys.executable, str(self.SCRIPTS / "rollback_closed_by.py"),
             str(path or self.path), *flags],
            capture_output=True, text=True, timeout=60)

    def closers_and_version(self):
        conn = sqlite3.connect(self.path)
        try:
            return (conn.execute("SELECT COUNT(closed_by) FROM trips").fetchone()[0],
                    conn.execute("PRAGMA user_version").fetchone()[0])
        finally:
            conn.close()

    def migrated(self):
        pre_migration_fixture(self.path)
        grocery.connect(self.path).close()
        self.assertEqual(self.closers_and_version(), (1, db.SCHEMA_VERSION))

    def test_rollback_needs_an_explicit_yes(self):
        """QA N4: no sqlite3 CLI on this box, so the rollback is a script."""
        self.migrated()
        result = self.rollback()
        self.assertEqual(result.returncode, 2)
        self.assertIn("--yes", result.stderr)
        self.assertEqual(self.closers_and_version(), (1, db.SCHEMA_VERSION))

    def test_rollback_clears_every_closer_and_prints_no_phone(self):
        self.migrated()
        result = self.rollback("--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual((report["trips"], report["closers_before"], report["closers_cleared"]),
                         (1, 1, 1))
        for phone in (CAIO, ALEX):
            self.assertNotIn(phone.lstrip("+"), result.stdout)
        # Without --reset-version the version stays, so a re-deploy will not refill.
        self.assertEqual(self.closers_and_version(), (0, db.SCHEMA_VERSION))

    def test_reset_version_lets_pre_migrate_recover_the_closers(self):
        self.migrated()
        result = self.rollback("--yes", "--reset-version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.closers_and_version(), (0, 0))
        forward = json.loads(self.pre_migrate(self.path).stdout)
        self.assertEqual(forward["trips"], {"total": 1, "with_closer": 1})
        self.assertEqual(self.closers_and_version(), (1, db.SCHEMA_VERSION))

    def test_rollback_will_not_create_a_database(self):
        missing = Path(self.temp.name) / "typo.sqlite3"
        result = self.rollback("--yes", path=missing)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(missing.exists())


class VersionTwoIndexTests(unittest.TestCase):
    """1 -> 2 adds the partial indexes retention probes, and nothing else."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "v1.sqlite3"
        version_one_fixture(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self):
        conn = sqlite3.connect(self.path)
        try:
            return {t: conn.execute(f"SELECT * FROM {t} ORDER BY id").fetchall()
                    for t in ("events", "item_sources", "items", "stores", "groups")}
        finally:
            conn.close()

    def indexes(self, conn):
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")}

    def test_version_one_gains_the_indexes_and_the_version(self):
        conn = sqlite3.connect(self.path)
        before = schema_of(conn)
        self.assertFalse(set(db.RETENTION_INDEXES) & self.indexes(conn))
        conn.close()

        conn = db.connect(self.path, purge=False)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0],
                         db.SCHEMA_VERSION)
        after = schema_of(conn)
        self.assertEqual({name for name, _ in after["<indexes>"] - before["<indexes>"]},
                         set(db.RETENTION_INDEXES))
        self.assertEqual({k: v for k, v in after.items() if k != "<indexes>"},
                         {k: v for k, v in before.items() if k != "<indexes>"})
        for name, definition in db.RETENTION_INDEXES.items():
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (name,)).fetchone()[0]
            self.assertTrue(" ".join(sql.split()).endswith(f"ON {definition}"), sql)
        conn.close()

    def test_the_upgrade_itself_touches_no_row(self):
        before = self.snapshot()
        db.connect(self.path, purge=False).close()
        self.assertEqual(self.snapshot(), before)

    def test_running_it_again_is_harmless_and_writes_nothing(self):
        db.connect(self.path, purge=False).close()
        schema = schema_of(sqlite3.connect(self.path))
        conn = db.connect(self.path, purge=False)
        self.assertEqual(conn.total_changes, 0)
        conn.close()
        # Forcing every step to run again, as a racing process would, is a no-op.
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()
        conn = db.connect(self.path, purge=False)
        self.assertEqual(schema_of(conn), schema)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0],
                         db.SCHEMA_VERSION)
        conn.close()

    def test_a_fresh_database_has_the_same_indexes(self):
        with tempfile.TemporaryDirectory() as root:
            fresh = grocery.connect(Path(root) / "fresh.sqlite3")
            self.assertLessEqual(set(db.RETENTION_INDEXES), self.indexes(fresh))
            fresh.close()


class QuietOpenTests(unittest.TestCase):
    """QA M3: opening an up-to-date database must not write to it."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "t.sqlite3"
        pre_migration_fixture(self.path)
        conn = grocery.connect(self.path)
        # A trip whose close left no attributed events: its closer is unknowable.
        conn.execute("INSERT INTO trips(store_id, closed_at) VALUES (1, ?)",
                     ("2026-01-01T00:00:00-05:00",))
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp.cleanup()

    def test_opening_a_migrated_database_changes_nothing(self):
        for _ in range(2):
            conn = grocery.connect(self.path)
            self.assertEqual(conn.total_changes, 0)
            conn.close()

    def test_the_closer_backfill_leaves_unknowable_trips_untouched(self):
        conn = grocery.connect(self.path)
        before = conn.total_changes
        db.record_trip_closers(conn)
        self.assertEqual(conn.total_changes, before,
                         "the backfill rewrote NULL closers to NULL")
        conn.rollback()
        conn.close()


if __name__ == "__main__":
    unittest.main()
