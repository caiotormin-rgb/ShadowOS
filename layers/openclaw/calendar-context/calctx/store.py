"""SQLite storage for the calendar-context read model.

Every write is idempotent so a replayed events.list page cannot corrupt the
index, and each calendar's syncToken is advanced only by the caller, after its
data transaction has committed.

Note what is absent: there is no method here that talks to Google, and no
method that could. This is the write side of *our own* index -- the thing the
read-only contract in `calctx.query` reads from -- and the only reason it has
INSERT and DELETE in it at all. See tests/test_no_write_path.py for the
distinction the no-write check actually enforces.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import SCHEMA_VERSION
from .timespec import TimeSpec

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


@dataclass(frozen=True)
class Calendar:
    calendar_id: str
    summary: str = ""
    time_zone: str = "UTC"
    selected: bool = True
    is_primary: bool = False
    access_role: str = ""


@dataclass(frozen=True)
class Event:
    """One row of the series layer. There is deliberately no description field."""

    calendar_id: str
    event_id: str
    kind: str                       # 'single' | 'series' | 'override'
    start: TimeSpec
    end: TimeSpec
    status: str = "confirmed"
    summary: str = ""
    location: str = ""
    ical_uid: str = ""
    etag: str = ""
    sequence: int | None = None
    recurring_event_id: str | None = None
    original_start: TimeSpec | None = None
    organizer_email: str = ""
    creator_email: str = ""
    self_response_status: str | None = None
    transparency: str = "opaque"
    visibility: str = "default"
    recurrence: Sequence[str] = field(default_factory=tuple)
    updated_ts: int | None = None
    attendees: Sequence[tuple[str, str, bool, bool]] = field(default_factory=tuple)


@dataclass(frozen=True)
class Instance:
    """One materialized occurrence. Overrides are denormalized onto this row so
    an agenda read never consults the series row to learn one occurrence moved."""

    calendar_id: str
    instance_id: str
    series_event_id: str
    start: TimeSpec
    end: TimeSpec
    order_ts: int
    status: str = "confirmed"
    is_override: bool = False
    summary: str = ""
    location: str = ""
    transparency: str = "opaque"
    self_response_status: str | None = None


def _spec_cols(spec: TimeSpec, prefix: str) -> dict[str, Any]:
    """TimeSpec -> the three columns the CHECK constraint expects."""
    if spec.kind == "timed":
        return {f"{prefix}_utc": spec.utc, f"{prefix}_tz": spec.tz, f"{prefix}_date": None}
    return {f"{prefix}_utc": None, f"{prefix}_tz": None, f"{prefix}_date": spec.date}


def connect(db_path: str | os.PathLike[str], *, read_only: bool = False) -> sqlite3.Connection:
    """Open the index. read_only=True is the agent-facing mode and is enforced
    by SQLite itself, not by convention."""
    path = Path(db_path)
    if read_only:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        existed = path.exists()
        conn = sqlite3.connect(path, isolation_level=None)
        if not existed:
            path.chmod(0o600)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if read_only:
        conn.execute("PRAGMA query_only = ON")
    else:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Apply the schema. Safe to run on every start."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"index schema v{current} is newer than this code (v{SCHEMA_VERSION}); refusing to touch it"
        )
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return SCHEMA_VERSION


class Store:
    """Write side of the index. Callers own transaction boundaries."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # -- transactions ----------------------------------------------------
    def begin(self) -> None:
        self.conn.execute("BEGIN IMMEDIATE")

    def commit(self) -> None:
        self.conn.execute("COMMIT")

    def rollback(self) -> None:
        self.conn.execute("ROLLBACK")

    def abort(self) -> bool:
        """Roll back only if a transaction is actually open.

        Error handlers need to record a failure, which means opening their own
        transaction -- but they may be reached with one already open, and BEGIN
        inside a transaction is an error. Returns whether it rolled back.
        """
        if self.conn.in_transaction:
            self.conn.execute("ROLLBACK")
            return True
        return False

    # -- calendars -------------------------------------------------------
    def upsert_calendar(self, cal: Calendar, *, now: int | None = None) -> str:
        now = int(time.time()) if now is None else now
        existed = self.conn.execute(
            "SELECT 1 FROM cal_calendars WHERE calendar_id = ?", (cal.calendar_id,)
        ).fetchone() is not None
        self.conn.execute(
            """INSERT INTO cal_calendars (calendar_id, summary, time_zone, selected,
                                          is_primary, access_role, synced_at, deleted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
               ON CONFLICT(calendar_id) DO UPDATE SET
                 summary     = excluded.summary,
                 time_zone   = excluded.time_zone,
                 selected    = excluded.selected,
                 is_primary  = excluded.is_primary,
                 access_role = excluded.access_role,
                 synced_at   = excluded.synced_at,
                 deleted_at  = NULL""",
            (cal.calendar_id, cal.summary, cal.time_zone, int(cal.selected),
             int(cal.is_primary), cal.access_role, now),
        )
        return "updated" if existed else "added"

    def tombstone_calendar(self, calendar_id: str, *, now: int | None = None) -> bool:
        now = int(time.time()) if now is None else now
        cur = self.conn.execute(
            "UPDATE cal_calendars SET deleted_at = ?, selected = 0 "
            "WHERE calendar_id = ? AND deleted_at IS NULL",
            (now, calendar_id),
        )
        return bool(cur.rowcount)

    def selected_calendars(self) -> list[sqlite3.Row]:
        """The user's own Calendar-UI selection is the scope control. We do not
        invent a second one."""
        return list(self.conn.execute(
            "SELECT * FROM cal_calendars WHERE selected = 1 AND deleted_at IS NULL "
            "ORDER BY is_primary DESC, calendar_id"
        ))

    def calendar_tz(self, calendar_id: str) -> str:
        row = self.conn.execute(
            "SELECT time_zone FROM cal_calendars WHERE calendar_id = ?", (calendar_id,)
        ).fetchone()
        return (row["time_zone"] if row and row["time_zone"] else "UTC")

    # -- series layer ----------------------------------------------------
    def upsert_event(self, ev: Event, *, now: int | None = None) -> str:
        """Insert or refresh one series-layer row. Returns 'added' or 'updated'.

        Re-applying the same event is a no-op beyond synced_at, which is what
        makes a replayed events.list page safe.
        """
        now = int(time.time()) if now is None else now
        existed = self.conn.execute(
            "SELECT 1 FROM cal_events WHERE calendar_id = ? AND event_id = ?",
            (ev.calendar_id, ev.event_id),
        ).fetchone() is not None

        params: dict[str, Any] = {
            "calendar_id": ev.calendar_id,
            "event_id": ev.event_id,
            "ical_uid": ev.ical_uid,
            "etag": ev.etag,
            "sequence": ev.sequence,
            "kind": ev.kind,
            "recurring_event_id": ev.recurring_event_id,
            "original_start_utc": ev.original_start.utc if ev.original_start else None,
            "original_start_date": ev.original_start.date if ev.original_start else None,
            "status": ev.status,
            "summary": ev.summary,
            "location": ev.location,
            "organizer_email": ev.organizer_email,
            "creator_email": ev.creator_email,
            "self_response_status": ev.self_response_status,
            "transparency": ev.transparency,
            "visibility": ev.visibility,
            "start_kind": ev.start.kind,
            # The recurrence array is stored exactly as received. Normalizing or
            # "cleaning up" an RRULE here would make the series layer a lossy
            # paraphrase of the rule instead of an auditable copy of it.
            "recurrence": json.dumps(list(ev.recurrence)) if ev.recurrence else None,
            "updated_ts": ev.updated_ts,
            "synced_at": now,
        }
        params.update(_spec_cols(ev.start, "start"))
        params.update(_spec_cols(ev.end, "end"))

        self.conn.execute(
            """INSERT INTO cal_events (
                   calendar_id, event_id, ical_uid, etag, sequence, kind, recurring_event_id,
                   original_start_utc, original_start_date, status, summary, location,
                   organizer_email, creator_email, self_response_status, transparency, visibility,
                   start_kind, start_utc, start_tz, start_date, end_utc, end_tz, end_date,
                   recurrence, updated_ts, synced_at, deleted_at)
               VALUES (
                   :calendar_id, :event_id, :ical_uid, :etag, :sequence, :kind, :recurring_event_id,
                   :original_start_utc, :original_start_date, :status, :summary, :location,
                   :organizer_email, :creator_email, :self_response_status, :transparency, :visibility,
                   :start_kind, :start_utc, :start_tz, :start_date, :end_utc, :end_tz, :end_date,
                   :recurrence, :updated_ts, :synced_at, NULL)
               ON CONFLICT(calendar_id, event_id) DO UPDATE SET
                   ical_uid            = excluded.ical_uid,
                   etag                = excluded.etag,
                   sequence            = excluded.sequence,
                   kind                = excluded.kind,
                   recurring_event_id  = excluded.recurring_event_id,
                   original_start_utc  = excluded.original_start_utc,
                   original_start_date = excluded.original_start_date,
                   status              = excluded.status,
                   summary             = excluded.summary,
                   location            = excluded.location,
                   organizer_email     = excluded.organizer_email,
                   creator_email       = excluded.creator_email,
                   self_response_status= excluded.self_response_status,
                   transparency        = excluded.transparency,
                   visibility          = excluded.visibility,
                   start_kind          = excluded.start_kind,
                   start_utc           = excluded.start_utc,
                   start_tz            = excluded.start_tz,
                   start_date          = excluded.start_date,
                   end_utc             = excluded.end_utc,
                   end_tz              = excluded.end_tz,
                   end_date            = excluded.end_date,
                   recurrence          = excluded.recurrence,
                   updated_ts          = excluded.updated_ts,
                   synced_at           = excluded.synced_at,
                   deleted_at          = NULL""",
            params,
        )
        self.set_attendees(ev.calendar_id, ev.event_id, ev.attendees)
        return "updated" if existed else "added"

    def set_attendees(self, calendar_id: str, event_id: str,
                      attendees: Iterable[tuple[str, str, bool, bool]]) -> None:
        """Applied, never accumulated: an RSVP change must not leave the old row."""
        self.conn.execute(
            "DELETE FROM cal_attendees WHERE calendar_id = ? AND event_id = ?",
            (calendar_id, event_id),
        )
        self.conn.executemany(
            """INSERT OR REPLACE INTO cal_attendees
                   (calendar_id, event_id, email, response_status, is_self, optional)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [(calendar_id, event_id, email, response, int(is_self), int(optional))
             for email, response, is_self, optional in attendees],
        )

    def tombstone_event(self, calendar_id: str, event_id: str, *, now: int | None = None) -> bool:
        """Google deleted the event outright (not a cancelled occurrence).

        The instances go with it, because an event that no longer exists is not
        the same thing as an occurrence that was cancelled -- and the schema's
        ON DELETE CASCADE only fires if we actually delete the instance rows.
        """
        now = int(time.time()) if now is None else now
        cur = self.conn.execute(
            "UPDATE cal_events SET deleted_at = ?, status = 'cancelled' "
            "WHERE calendar_id = ? AND event_id = ? AND deleted_at IS NULL",
            (now, calendar_id, event_id),
        )
        self.conn.execute(
            "DELETE FROM cal_instances WHERE calendar_id = ? AND series_event_id = ?",
            (calendar_id, event_id),
        )
        return bool(cur.rowcount)

    def event_row(self, calendar_id: str, event_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM cal_events WHERE calendar_id = ? AND event_id = ?",
            (calendar_id, event_id),
        ).fetchone()

    def live_series(self, calendar_id: str) -> list[sqlite3.Row]:
        """Every non-deleted master and single in one calendar, for a full
        re-materialization after the window moves."""
        return list(self.conn.execute(
            "SELECT * FROM cal_events WHERE calendar_id = ? AND deleted_at IS NULL "
            "AND kind != 'override' ORDER BY event_id",
            (calendar_id,),
        ))

    # -- materialized layer ----------------------------------------------
    def replace_instances(self, calendar_id: str, series_event_id: str,
                          instances: Sequence[Instance], *, now: int | None = None) -> int:
        """Full replace of one series' occurrences. Caller holds the transaction.

        Replace rather than merge: a partial expansion that merged would leave
        occurrences behind after an RRULE shortened, and those orphans read
        exactly like real meetings.
        """
        now = int(time.time()) if now is None else now
        self.conn.execute(
            "DELETE FROM cal_instances WHERE calendar_id = ? AND series_event_id = ?",
            (calendar_id, series_event_id),
        )
        for inst in instances:
            params: dict[str, Any] = {
                "calendar_id": inst.calendar_id,
                "instance_id": inst.instance_id,
                "series_event_id": inst.series_event_id,
                "start_kind": inst.start.kind,
                "order_ts": inst.order_ts,
                "status": inst.status,
                "is_override": int(inst.is_override),
                "summary": inst.summary,
                "location": inst.location,
                "transparency": inst.transparency,
                "self_response_status": inst.self_response_status,
                "synced_at": now,
            }
            params.update(_spec_cols(inst.start, "start"))
            params.update(_spec_cols(inst.end, "end"))
            self.conn.execute(
                """INSERT INTO cal_instances (
                       calendar_id, instance_id, series_event_id, start_kind,
                       start_utc, start_tz, start_date, end_utc, end_tz, end_date,
                       order_ts, status, is_override, summary, location, transparency,
                       self_response_status, synced_at)
                   VALUES (
                       :calendar_id, :instance_id, :series_event_id, :start_kind,
                       :start_utc, :start_tz, :start_date, :end_utc, :end_tz, :end_date,
                       :order_ts, :status, :is_override, :summary, :location, :transparency,
                       :self_response_status, :synced_at)""",
                params,
            )
        return len(instances)

    def materialized_state(self, calendar_id: str, event_id: str) -> tuple[str | None, int]:
        """(etag at last expansion, instances currently stored) for one series.

        Used ONLY by the initial load's resume skip. An incremental run must
        never consult it: history told us something changed, and the stored row
        is exactly the stale one.
        """
        row = self.conn.execute(
            "SELECT materialized_etag FROM cal_events WHERE calendar_id = ? AND event_id = ?",
            (calendar_id, event_id),
        ).fetchone()
        count = self.conn.execute(
            "SELECT COUNT(*) FROM cal_instances WHERE calendar_id = ? AND series_event_id = ?",
            (calendar_id, event_id),
        ).fetchone()[0]
        return ((row["materialized_etag"] if row else None), int(count))

    def mark_materialized(self, calendar_id: str, event_id: str, etag: str | None,
                          *, now: int | None = None) -> None:
        now = int(time.time()) if now is None else now
        self.conn.execute(
            "UPDATE cal_events SET materialized_etag = ?, materialized_at = ? "
            "WHERE calendar_id = ? AND event_id = ?",
            (etag, now, calendar_id, event_id),
        )

    # -- retention -------------------------------------------------------
    def prune(self, *, window_start_ts: int, window_end_ts: int,
              tombstone_grace: int = 7 * 86400, now: int | None = None) -> int:
        """Drop occurrences outside the rolling window, then series with none left.

        Three rows are deliberately *not* eligible, and each guard was earned:

        * **Never materialized.** An event whose instances have not been
          expanded yet has no in-window occurrences simply because nothing has
          looked. Pruning it would wipe the progress of an interrupted initial
          load on the very next run, which is exactly what the resume skip
          exists to preserve.
        * **A master with an in-window instance**, even if the master itself
          started before the window.
        * **An override whose parent still has instances** -- deleting the row
          that explains why one occurrence differs would let the next
          re-materialization silently reinstate the un-overridden time.

        Deletion tombstones are held for a short grace period so a replayed page
        can still reconcile against them.
        """
        now = int(time.time()) if now is None else now
        cur = self.conn.execute(
            "DELETE FROM cal_instances WHERE order_ts < ? OR order_ts >= ?",
            (window_start_ts, window_end_ts),
        )
        pruned = cur.rowcount
        self.conn.execute(
            """DELETE FROM cal_events
                WHERE (materialized_at IS NOT NULL OR kind = 'override')
                  AND (deleted_at IS NULL OR deleted_at < :cutoff)
                  AND NOT EXISTS (SELECT 1 FROM cal_instances i
                                   WHERE i.calendar_id = cal_events.calendar_id
                                     AND i.series_event_id = cal_events.event_id)
                  AND NOT EXISTS (SELECT 1 FROM cal_instances p
                                   WHERE p.calendar_id = cal_events.calendar_id
                                     AND p.series_event_id = cal_events.recurring_event_id)""",
            {"cutoff": now - tombstone_grace},
        )
        return pruned

    # -- sync bookkeeping ------------------------------------------------
    def start_run(self, kind: str, *, calendar_id: str | None = None,
                  now: int | None = None) -> int:
        now = int(time.time()) if now is None else now
        cur = self.conn.execute(
            "INSERT INTO cal_sync_runs (kind, calendar_id, started_at) VALUES (?, ?, ?)",
            (kind, calendar_id, now),
        )
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, *, status: str, added: int = 0, updated: int = 0,
                   deleted: int = 0, pruned: int = 0, instances: int = 0,
                   error_class: str | None = None, now: int | None = None) -> None:
        now = int(time.time()) if now is None else now
        self.conn.execute(
            """UPDATE cal_sync_runs SET finished_at = ?, status = ?, added = ?, updated = ?,
                   deleted = ?, pruned = ?, instances = ?, error_class = ? WHERE run_id = ?""",
            (now, status, added, updated, deleted, pruned, instances, error_class, run_id),
        )

    def calendar_cursor(self, calendar_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM cal_calendar_sync WHERE calendar_id = ?", (calendar_id,)
        ).fetchone()

    def advance_calendar_cursor(self, calendar_id: str, sync_token: str | None, *,
                                anchor_start_ts: int, anchor_end_ts: int,
                                now: int | None = None) -> None:
        """Store one calendar's syncToken together with the window that minted it.

        Call ONLY after the data transaction for the final page has committed.
        A crash before this point leaves the previous token in place and the
        next run replays -- which is safe precisely because every write above is
        idempotent.

        The anchor is the window computed BEFORE the listing began, not
        recomputed now. Recomputing here would record bounds wider than the
        request actually used and mark the far edge as covered when it is not.
        """
        now = int(time.time()) if now is None else now
        self.conn.execute(
            """INSERT INTO cal_calendar_sync (calendar_id, sync_token, anchor_start_ts,
                                              anchor_end_ts, last_success_at, status, error_class)
               VALUES (?, ?, ?, ?, ?, 'ok', NULL)
               ON CONFLICT(calendar_id) DO UPDATE SET
                 sync_token      = excluded.sync_token,
                 anchor_start_ts = excluded.anchor_start_ts,
                 anchor_end_ts   = excluded.anchor_end_ts,
                 last_success_at = excluded.last_success_at,
                 status          = 'ok',
                 error_class     = NULL""",
            (calendar_id, sync_token, anchor_start_ts, anchor_end_ts, now),
        )

    def clear_calendar_token(self, calendar_id: str) -> None:
        """Discard an expired (410 GONE) token without losing the indexed data."""
        self.conn.execute(
            "UPDATE cal_calendar_sync SET sync_token = NULL WHERE calendar_id = ?",
            (calendar_id,),
        )

    def record_calendar_failure(self, calendar_id: str, error_class: str) -> None:
        """One calendar failing must not disturb the others' cursors."""
        self.conn.execute(
            """INSERT INTO cal_calendar_sync (calendar_id, status, error_class)
               VALUES (?, 'error', ?)
               ON CONFLICT(calendar_id) DO UPDATE SET status = 'error', error_class = excluded.error_class""",
            (calendar_id, error_class),
        )

    def advance_global(self, *, window_start_ts: int, window_end_ts: int,
                       kind: str = "incremental", now: int | None = None) -> None:
        now = int(time.time()) if now is None else now
        full = ", last_full_sync_at = :now" if kind in ("initial", "resync") else ""
        self.conn.execute(
            f"""UPDATE cal_sync_state SET last_success_at = :now, window_start_ts = :ws,
                    window_end_ts = :we, status = 'ok', error_class = NULL{full} WHERE id = 1""",
            {"now": now, "ws": window_start_ts, "we": window_end_ts},
        )

    def record_failure(self, error_class: str) -> None:
        self.conn.execute(
            "UPDATE cal_sync_state SET status = 'error', error_class = ? WHERE id = 1",
            (error_class,),
        )

    def set_window(self, window_start_ts: int, window_end_ts: int) -> None:
        self.conn.execute(
            "UPDATE cal_sync_state SET window_start_ts = ?, window_end_ts = ? WHERE id = 1",
            (window_start_ts, window_end_ts),
        )
