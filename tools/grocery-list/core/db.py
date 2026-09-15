"""Connection, schema, migrations, and the append-only event log."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import retention
import sections
import synonyms
from text import normalized, now_iso, parse_timestamp


APP_DIR = Path(__file__).resolve().parent

DEFAULT_DB = Path(os.environ.get("GROCERY_DB") or APP_DIR / "data" / "grocery.sqlite3")
# The scratch list development tools use, so experimenting never touches live.
DEV_DB = APP_DIR / "data" / "dev.sqlite3"


# A database at this version skips migration entirely, so opening one is a
# single read of the header. That makes the bump rule load-bearing:
#
#   Any change to SCHEMA or apply_migrations -> SCHEMA_VERSION += 1, and record
#   the new schema in test_migration.MIGRATED_COLUMNS.
#
# Forget the bump and the new step never runs on an existing database; only a
# fresh one gets it. test_migration fails when the migrated schema changes but
# this number does not.
#
# 2: partial indexes that find message text past its retention period (retention.py).
# 3: validated product URLs on live, archived, and event rows.
# 4: refresh canonical product keys and merge newly recognized synonyms.
SCHEMA_VERSION = 4


def connect(db_path: str | Path, purge: bool = True,
            timeout: float = 30) -> sqlite3.Connection:
    """Open the list, upgrade its schema if behind, and blank expired message text.

    `purge=False` is for callers that run and report the purge themselves
    (scripts/purge_raw_text.py) or must observe a database as it was.
    `timeout` is how long, in seconds, to wait for another process's lock.
    """
    path = Path(db_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Every WhatsApp message is its own engine process, so opens overlap; wait
    # for another process's migration instead of failing on its lock.
    conn = sqlite3.connect(path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
        migrate(conn)
    if purge:
        # One index probe and no write unless text is past retention.
        retention.purge_expired(conn)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Bring the schema up to SCHEMA_VERSION, exactly once, even under a race.

    Check-then-ALTER without a lock let two processes both see a column
    missing and both add it; the loser's request failed. So the whole upgrade
    runs in one BEGIN IMMEDIATE transaction — which admits one writer — and
    re-reads the version inside it, because whoever held the lock first has
    usually finished the job. Foreign keys are off for the duration: the stores
    rebuild must not cascade, and the pragma cannot change inside a transaction.
    """
    previous = conn.isolation_level
    conn.isolation_level = None          # this function owns the transaction
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
                apply_migrations(conn)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.isolation_level = previous


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Every step, in order. Each is idempotent; none commits."""
    for statement in _statements(SCHEMA):
        conn.execute(statement)
    ensure_column(conn, "stores", "layout",
                  f"layout TEXT NOT NULL DEFAULT '{sections.DEFAULT_LAYOUT}'")
    scope_stores_to_groups(conn)
    add_canonical_names(conn)
    ensure_column(conn, "people", "default_store", "default_store TEXT NOT NULL DEFAULT ''")
    scope_events_to_groups(conn)
    # Empty means "not overridden": the zone is guessed from the phone's
    # country code at read time (people.timezone_for), so nothing to backfill.
    ensure_column(conn, "people", "timezone", "timezone TEXT NOT NULL DEFAULT ''")
    record_trip_closers(conn)
    # The message a change came from. add kept it only in item_sources, which
    # cascades away with the item; buy and remove kept it nowhere. `note` stays
    # the item's own note. Old rows keep '' — the text was never stored.
    ensure_column(conn, "events", "raw_text", "raw_text TEXT NOT NULL DEFAULT ''")
    # What a merge replaced. `quantity` on a merged row is only the result, so
    # "12 -> 24" was unrecoverable. NULL on rows logged before this existed.
    ensure_column(conn, "events", "previous_quantity", "previous_quantity REAL")
    # The agent fetches product pages with OpenClaw's bounded web tools and
    # hands this engine only normalized data. These are storage fields, not a
    # network client: keeping URL handling here avoids creating an SSRF path.
    ensure_column(conn, "items", "product_url",
                  "product_url TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "trip_items", "product_url",
                  "product_url TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "events", "product_url",
                  "product_url TEXT NOT NULL DEFAULT ''")
    refresh_canonical_names(conn)
    index_message_text_by_age(conn)


# The rows still holding member text (retention.FIELDS), by when it was
# written — or, for archived trip items, by trip. Partial, so a purged row
# leaves the index: "is anything past retention?" stays a short index probe
# however long the history grows. Each WHERE must match retention.py's query
# term for term, or SQLite will not use the index. Part of SCHEMA_VERSION 2,
# amended before 2 was released (QA H1): no database was ever at 2 without them.
RETENTION_INDEXES = {
    "events_raw_text_age": "events(occurred_at) WHERE raw_text != ''",
    "item_sources_raw_text_age": "item_sources(observed_at) WHERE raw_text != ''",
    "events_note_age": "events(occurred_at) WHERE note != ''",
    "trip_items_note_trip": "trip_items(trip_id) WHERE note != ''",
}


def index_message_text_by_age(conn: sqlite3.Connection) -> None:
    for name, definition in RETENTION_INDEXES.items():
        conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {definition}")


def _statements(script: str):
    """Split a schema script into single statements for conn.execute.

    executescript would commit the migration's transaction before running.
    """
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            yield buffer
            buffer = ""


SCHEMA = """
        CREATE TABLE IF NOT EXISTS stores (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );

        -- A household. Everything a person can reach hangs off their group
        -- membership, so scoping stores to a group scopes the items, trips,
        -- and shares that reference them without touching those tables.
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS group_members (
            id INTEGER PRIMARY KEY,
            group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
            actor TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member'
                CHECK(role IN ('owner', 'member')),
            added_at TEXT NOT NULL,
            UNIQUE(group_id, actor)
        );

        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY,
            store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            quantity REAL NOT NULL DEFAULT 1,
            unit TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'needed'
                CHECK(status IN ('needed', 'purchased')),
            first_added_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(store_id, normalized_name, unit)
        );

        CREATE TABLE IF NOT EXISTS item_sources (
            id INTEGER PRIMARY KEY,
            item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            source_type TEXT NOT NULL,
            source_ref TEXT NOT NULL DEFAULT '',
            raw_text TEXT NOT NULL DEFAULT '',
            observed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS trips (
            id INTEGER PRIMARY KEY,
            store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
            closed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS trip_items (
            id INTEGER PRIMARY KEY,
            trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            quantity REAL NOT NULL,
            unit TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            outcome TEXT NOT NULL CHECK(outcome IN ('purchased', 'missing')),
            first_added_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY,
            alias TEXT NOT NULL,
            normalized_alias TEXT NOT NULL UNIQUE,
            channel TEXT NOT NULL,
            target TEXT NOT NULL,
            added_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1))
        );

        CREATE TABLE IF NOT EXISTS shares (
            id INTEGER PRIMARY KEY,
            store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
            contact_id INTEGER NOT NULL REFERENCES contacts(id),
            rendered_at TEXT NOT NULL,
            delivered_at TEXT
        );

        -- Append-only history. Deliberately carries no foreign keys: item and
        -- store names are denormalized so that deleting an item, closing a
        -- trip, or dropping a store can never cascade history away.
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY,
            occurred_at TEXT NOT NULL,
            store TEXT NOT NULL,
            item_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            unit TEXT NOT NULL DEFAULT '',
            quantity REAL,
            action TEXT NOT NULL CHECK(action IN (
                'added', 'merged', 'purchased', 'unpurchased', 'removed',
                'trip_purchased', 'trip_missing', 'reopened'
            )),
            source_type TEXT NOT NULL DEFAULT '',
            source_ref TEXT NOT NULL DEFAULT '',
            actor TEXT,
            trip_id INTEGER,
            note TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS events_item
            ON events(normalized_name, occurred_at);
        CREATE INDEX IF NOT EXISTS events_store
            ON events(store, occurred_at);
        CREATE INDEX IF NOT EXISTS events_action
            ON events(action, occurred_at);

        -- Learned product -> section mapping, keyed by normalized name rather
        -- than by item id, so a classification survives the item being bought,
        -- archived, and deleted. The household's vocabulary accumulates here.
        -- Who uses this list, and how they want to be answered. Keyed by the
        -- same actor string the event log records, so idea 2 can grow this
        -- into the identity registry without moving the language column.
        CREATE TABLE IF NOT EXISTS people (
            actor TEXT PRIMARY KEY,
            display_name TEXT NOT NULL DEFAULT '',
            lang TEXT NOT NULL DEFAULT 'en' CHECK(lang IN ('en', 'pt')),
            added_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS product_sections (
            normalized_name TEXT PRIMARY KEY,
            section TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'curated'
                CHECK(source IN ('curated', 'agent', 'user')),
            learned_at TEXT NOT NULL
        );
"""


def record_trip_closers(conn: sqlite3.Connection) -> None:
    """Give every trip the person who closed it, recovering old ones from the log.

    Nullable: a trip whose close left no attributed events stays unknown rather
    than being credited to a guess. The backfill matches the close's own events
    by trip id *and* timestamp, because `reopen` deletes a trip and SQLite hands
    the next close the same id — the log then holds two closes under one
    trip_id, and only the one written at this trip's closed_at is this trip's.
    It also requires the event's household to be the trip's, so a colliding id
    elsewhere cannot supply the name. Part of the migration; touches only NULLs.
    """
    ensure_column(conn, "trips", "closed_by", "closed_by TEXT")
    # Only trips with a recoverable closer are written. Updating the rest set
    # NULL to NULL: a write, and a held transaction, for no information.
    conn.execute(
        """
        WITH closers AS (
            SELECT trips.id AS trip_id, (
                SELECT e.actor FROM events e
                JOIN stores s ON s.id = trips.store_id
                WHERE e.trip_id = trips.id
                  AND e.occurred_at = trips.closed_at
                  AND e.action IN ('trip_purchased', 'trip_missing')
                  AND e.group_id = s.group_id
                  AND e.actor IS NOT NULL AND e.actor != ''
                ORDER BY e.id DESC LIMIT 1
            ) AS actor
            FROM trips WHERE trips.closed_by IS NULL
        )
        UPDATE trips
        SET closed_by = (SELECT actor FROM closers WHERE closers.trip_id = trips.id)
        WHERE trips.id IN (SELECT trip_id FROM closers WHERE actor IS NOT NULL)
        """
    )


def scope_events_to_groups(conn: sqlite3.Connection) -> None:
    """Give every event the household it belongs to.

    The log denormalizes the store *name*, which stopped identifying a store
    the moment two households were allowed their own Costco. Filtering history
    by name therefore showed one household the other's purchases. A plain
    integer, not a foreign key, so the log keeps its property that nothing
    cascades history away.
    """
    ensure_column(conn, "events", "group_id", "group_id INTEGER")
    # Backfill only where the name is unambiguous; a colliding name has no
    # right answer, and guessing one would attribute history to the wrong
    # household. Those rows stay NULL and are visible to nobody.
    conn.execute(
        """
        UPDATE events SET group_id = (
            SELECT MIN(s.group_id) FROM stores s WHERE s.name = events.store
        )
        WHERE group_id IS NULL AND (
            SELECT COUNT(*) FROM stores s WHERE s.name = events.store
        ) = 1
        """
    )


def add_canonical_names(conn: sqlite3.Connection) -> None:
    """Give every item a cross-language dedup key, backfilling existing rows.

    Added as a plain column rather than a new UNIQUE constraint: the existing
    (store, normalized_name, unit) uniqueness stays as a safety net, and the
    canonical key is what lookups actually match on. That avoids a second table
    rebuild for a property that is allowed to change as the synonym map grows.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(items)")}
    if "canonical_name" in columns:
        # Repair rows written before the buy-an-unlisted-item path set the key.
        # An empty key matches nothing, so those items silently stopped merging.
        blank = conn.execute(
            "SELECT id, name FROM items WHERE canonical_name = ''"
        ).fetchall()
        for row in blank:
            conn.execute(
                "UPDATE items SET canonical_name = ? WHERE id = ?",
                (synonyms.canonical(row["name"]), row["id"]),
            )
        return
    conn.execute("ALTER TABLE items ADD COLUMN canonical_name TEXT NOT NULL DEFAULT ''")
    for row in conn.execute("SELECT id, name FROM items").fetchall():
        conn.execute(
            "UPDATE items SET canonical_name = ? WHERE id = ?",
            (synonyms.canonical(row["name"]), row["id"]),
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS items_canonical "
        "ON items(store_id, canonical_name, unit)"
    )


def refresh_canonical_names(conn: sqlite3.Connection) -> None:
    """Apply the current synonym map to old rows and collapse new matches.

    Canonical keys are stored, so expanding the curated map does not affect an
    item already on a list until a migration rewrites it. Two spellings may
    then acquire the same key. Keep the first row, as normal ingestion does,
    move every source onto it, and combine the mutable fields with the same
    loss-averse rules used while adding an item.
    """
    rows = conn.execute("SELECT * FROM items ORDER BY id").fetchall()
    groups: dict[tuple[int, str, str], list[sqlite3.Row]] = {}
    for row in rows:
        key = (row["store_id"], synonyms.canonical(row["name"]), row["unit"])
        groups.setdefault(key, []).append(row)

    for (_, canonical, _), matches in groups.items():
        survivor = matches[0]
        quantity = float(survivor["quantity"])
        note = survivor["note"]
        product_url = survivor["product_url"]
        status = survivor["status"]
        updated_at = survivor["updated_at"]

        for duplicate in matches[1:]:
            quantity = max(quantity, float(duplicate["quantity"]))
            note = duplicate["note"] or note
            product_url = duplicate["product_url"] or product_url
            if duplicate["status"] == "needed":
                status = "needed"
            updated_at = max(
                updated_at, duplicate["updated_at"], key=parse_timestamp)
            conn.execute(
                "UPDATE item_sources SET item_id = ? WHERE item_id = ?",
                (survivor["id"], duplicate["id"]),
            )
            conn.execute("DELETE FROM items WHERE id = ?", (duplicate["id"],))

        desired = (canonical, quantity, note, product_url, status, updated_at)
        current = (
            survivor["canonical_name"], float(survivor["quantity"]),
            survivor["note"], survivor["product_url"], survivor["status"],
            survivor["updated_at"],
        )
        if desired != current:
            conn.execute(
                """
                UPDATE items
                SET canonical_name = ?, quantity = ?, note = ?, product_url = ?,
                    status = ?, updated_at = ?
                WHERE id = ?
                """,
                (*desired, survivor["id"]),
            )


DEFAULT_GROUP = os.environ.get("GROCERY_GROUP", "Household").strip() or "Household"


def scope_stores_to_groups(conn: sqlite3.Connection) -> None:
    """Give every store a group, rebuilding the table if it predates them.

    `stores.normalized_name` was globally UNIQUE, which is wrong once two
    households share a database — each may have its own Costco. SQLite cannot
    alter a constraint, so the table is rebuilt once, carrying its rows over.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(stores)")}
    if "group_id" in columns:
        return

    group_id = conn.execute(
        "SELECT id FROM groups WHERE normalized_name = ?",
        (DEFAULT_GROUP.casefold(),),
    ).fetchone()
    if group_id is None:
        cursor = conn.execute(
            "INSERT INTO groups(name, normalized_name, created_at) VALUES (?, ?, ?)",
            (DEFAULT_GROUP, DEFAULT_GROUP.casefold(), now_iso()),
        )
        group_id = cursor.lastrowid
    else:
        group_id = group_id["id"]

    # Foreign keys are already off: migrate() turns them off around the whole
    # upgrade, so dropping the old table cascades nothing.
    conn.execute(
        """
        CREATE TABLE stores_new (
            id INTEGER PRIMARY KEY,
            group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            layout TEXT NOT NULL DEFAULT 'supermarket',
            UNIQUE(group_id, normalized_name)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO stores_new(id, group_id, name, normalized_name, created_at, layout)
        SELECT id, ?, name, normalized_name, created_at, layout FROM stores
        """,
        (group_id,),
    )
    conn.execute("DROP TABLE stores")
    conn.execute("ALTER TABLE stores_new RENAME TO stores")


def ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Add a column to an existing table if it is not already there.

    migrate() serializes upgrades, but anything that calls this outside it can
    still lose the race between looking and altering; if the column turns up
    in between, that is success, not an error.
    """
    if column in _columns(conn, table):
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc) or column not in _columns(conn, table):
            raise


def _columns(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def begin_write(conn: sqlite3.Connection) -> bool:
    """Take the write lock *before* reading what a write depends on.

    Every message is its own process. "Is this item on the list? No, insert
    it" run by two processes at once let both see no row and both insert, and
    the second died on the items UNIQUE constraint. BEGIN IMMEDIATE admits one
    writer at a time (the other waits, up to connect()'s timeout), so the
    second sees the first's row and merges into it.

    Joins a transaction the caller already has open rather than nesting one.
    Returns whether it began a transaction, so a path that ends without
    writing knows it is the one to release the lock.
    """
    if conn.in_transaction:
        return False
    conn.execute("BEGIN IMMEDIATE")
    return True


def record_event(
    conn: sqlite3.Connection,
    *,
    occurred_at: str,
    store: str,
    name: str,
    action: str,
    quantity: float | None = None,
    unit: str = "",
    note: str = "",
    source_type: str = "",
    source_ref: str = "",
    actor: str | None = None,
    trip_id: int | None = None,
    group_id: int | None = None,
    raw_text: str = "",
    previous_quantity: float | None = None,
    product_url: str = "",
) -> None:
    """Append one history row, owned by the household whose store it names."""
    conn.execute(
        """
        INSERT INTO events(
            occurred_at, store, item_name, normalized_name, unit, quantity,
            action, source_type, source_ref, actor, trip_id, note, group_id,
            raw_text, previous_quantity, product_url
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            occurred_at, store, name, normalized(name), unit, quantity,
            action, source_type, source_ref, actor, trip_id, note, group_id,
            raw_text, previous_quantity, product_url,
        ),
    )
