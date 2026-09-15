"""How long member-authored text is kept: 90 days, then it is blanked.

Owner decision (2026-09-13): the text or transcript a change came from is kept
for grocery history for 90 days and then purged; household privacy is a hard
requirement. The change itself — who, what, how many, when — stays: history,
`activity`, `due` and `stats` are built on it.

What is purged, and why each
----------------------------

- `events.raw_text` — the message or voice/video transcript behind an add,
  merge, buy, unbuy or remove. The agent is told to put it there
  (tools/grocery-list/skill/SKILL.md). Clock: `occurred_at`.
- `item_sources.raw_text` — the same text, recorded as an item's provenance.
  Clock: `observed_at`.
- `events.note` — the item's note as it stood when the change was logged,
  on *every* event row, whatever its `source_type`. Notes are not trusted to be
  short: the skills used to tell the agent to put the member's raw phrase in
  `note` (QA H1), and a member can still dictate one. A note only ever enters
  through an add or ingest — a message — and buy, close, reopen and remove then
  copy it with an empty `source_type` (20 of the 28 noted events on the live
  list were such copies). Keying the rule on `source_type` or `raw_text` would
  miss exactly those. Clock: `occurred_at`.
- `trip_items.note` — the item's note when the trip closed. Clock: that trip's
  `closed_at`, so an archived trip loses its notes 90 days after it closed.

Kept:

- `items.note` — the note on the live list, which members see and edit.
  Blanking it would change the list rather than forget a message; its copies in
  history go on the clock above. It goes when the item leaves the list.
- `source_type` (a category) and `source_ref` (a pointer: message id or media
  file name, never content). The media it names is OpenClaw's to expire.
- Item, store, person and contact names — the list's own vocabulary.

The engine writes no log files; the webtest server logs request lines to
stderr, which carry no member text.

Blanked, not NULLed: every one of these columns is NOT NULL DEFAULT '', and ''
already means "nothing stored" (rows from before the history feature have it).
Making them nullable would rebuild the append-only log for no gain.

When it runs
------------

`db.connect` calls `purge_expired` on every open. Nothing is due almost always,
so that is one probe per field of a partial index (db.RETENTION_INDEXES) and no
write — opening a database stays write-free. When something is due, one BEGIN
IMMEDIATE transaction blanks everything past the cutoff; a racing process waits
for the lock and then finds nothing left to do. `scripts/purge_raw_text.py`
runs the same purge by hand or from cron, and reports counts.

The setting fails closed
------------------------

GROCERY_RAW_TEXT_RETENTION_DAYS must be a whole number of days, 1-3650. Any
other value makes every engine command — reads included — exit with an error
naming the variable, until it is fixed or unset. A mistyped shorter period must
not silently keep text for longer.
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from errors import GroceryError


DEFAULT_DAYS = 90
MIN_DAYS, MAX_DAYS = 1, 3650
ENV = "GROCERY_RAW_TEXT_RETENTION_DAYS"


@dataclass(frozen=True)
class Field:
    table: str
    column: str
    stamp: str      # SQL, per row of `table`: when this text was written
    index: str      # the partial index (db.RETENTION_INDEXES) the probe uses


FIELDS: dict[str, Field] = {
    "events.raw_text": Field("events", "raw_text", "occurred_at", "events_raw_text_age"),
    "item_sources.raw_text": Field("item_sources", "raw_text", "observed_at",
                                   "item_sources_raw_text_age"),
    "events.note": Field("events", "note", "occurred_at", "events_note_age"),
    "trip_items.note": Field(
        "trip_items", "note",
        "(SELECT closed_at FROM trips WHERE trips.id = trip_items.trip_id)",
        "trip_items_note_trip"),
}


def retention_days(environ: dict | None = None) -> int:
    """The retention period: DEFAULT_DAYS, or a whole number of days from ENV.

    A bad value is an error, not a silent fallback: see "fails closed" above.
    """
    raw = (os.environ if environ is None else environ).get(ENV)
    if raw is None or not raw.strip():
        return DEFAULT_DAYS
    text = raw.strip()
    # [0-9], not \d: int() would accept other scripts' digits too.
    if not re.fullmatch(r"[0-9]{1,4}", text) or not MIN_DAYS <= int(text) <= MAX_DAYS:
        raise GroceryError(
            f"{ENV} must be a whole number of days from {MIN_DAYS} to {MAX_DAYS}"
        )
    return int(text)


def cutoff(now: datetime | None = None, days: int | None = None) -> datetime:
    """Text written strictly before this instant is past retention.

    A row exactly `days` old is still kept; one second older is not.
    """
    days = retention_days() if days is None else days
    moment = now if now is not None else datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0) - timedelta(days=days)


def _where(field: Field) -> str:
    # Stored stamps carry their own offsets, so string order is not time order.
    # The string bound is a date two days past the cutoff — wider than any UTC
    # offset — and lets a range index narrow; unixepoch() decides exactly.
    # `<column> != ''` must stay spelled as in the index definition.
    return (f"{field.column} != '' AND {field.stamp} < ? "
            f"AND unixepoch({field.stamp}) < ?")


def _params(limit: datetime) -> tuple[str, int]:
    return (limit + timedelta(days=2)).date().isoformat(), int(limit.timestamp())


def exists_sql(name: str) -> str:
    field = FIELDS[name]
    return f"SELECT 1 FROM {field.table} WHERE {_where(field)} LIMIT 1"


def due(conn: sqlite3.Connection, now: datetime | None = None,
        days: int | None = None) -> dict[str, int]:
    """How many rows per field hold text past retention. Reads only."""
    params = _params(cutoff(now, days))
    return {
        name: conn.execute(
            f"SELECT COUNT(*) FROM {field.table} WHERE {_where(field)}", params).fetchone()[0]
        for name, field in FIELDS.items()
    }


def purge(conn: sqlite3.Connection, now: datetime | None = None,
          days: int | None = None) -> dict[str, int]:
    """Blank member text past retention; returns rows blanked per field.

    Writes nothing, and takes no lock, unless a row is actually due.
    """
    params = _params(cutoff(now, days))
    if not any(conn.execute(exists_sql(name), params).fetchone() for name in FIELDS):
        return {name: 0 for name in FIELDS}
    if conn.in_transaction:
        raise GroceryError("purge needs a connection with no open transaction")

    previous_isolation = conn.isolation_level
    previous_secure = conn.execute("PRAGMA secure_delete").fetchone()[0]
    conn.isolation_level = None        # this function owns the transaction
    # Zero the old text's bytes in the page instead of leaving them in free space.
    conn.execute("PRAGMA secure_delete = ON")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # The WHERE re-checks under the lock: a process that raced us here
            # finds the rows already blank and changes nothing.
            purged = {
                name: conn.execute(
                    f"UPDATE {field.table} SET {field.column} = '' WHERE {_where(field)}",
                    params,
                ).rowcount
                for name, field in FIELDS.items()
            }
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.execute(f"PRAGMA secure_delete = {int(previous_secure)}")
        conn.isolation_level = previous_isolation
    return purged


# How long an open waits for the write lock before skipping its purge. connect()
# waits 30 s for real writes; a purge that waited that long turned a read into a
# 30 s request whenever text was due and anyone was writing — past the plugin's
# 15 s timeout (QA H2). The purge can always wait for the next open.
ENGINE_BUSY_MS = 1000


def purge_expired(conn: sqlite3.Connection) -> None:
    """The engine's opportunistic purge, run as every command opens the list.

    Waits at most ENGINE_BUSY_MS for the write lock, then skips silently: the
    member's request goes ahead and the next open tries again. The connection's
    own busy timeout is restored either way. Nothing is due almost always, and
    then no lock is requested at all.
    """
    previous = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    conn.execute(f"PRAGMA busy_timeout = {ENGINE_BUSY_MS}")
    try:
        purge(conn)
    except sqlite3.OperationalError as exc:
        if "locked" not in str(exc) and "busy" not in str(exc):
            raise
    finally:
        conn.execute(f"PRAGMA busy_timeout = {int(previous)}")
