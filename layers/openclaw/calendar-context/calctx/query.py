"""The read-only query contract exposed to the agent.

The agent gets these six methods and nothing else -- no SQL string, no database
path, no filesystem handle, no token. The connection is opened `mode=ro` with
`query_only=ON`, so the restriction is enforced by SQLite rather than by
politeness.

There is no create, insert, update, patch, delete, move, import, quickAdd, or
respond method here, and `tests/test_no_write_path.py` asserts that against the
class rather than against this paragraph.

Two rules shape every row that leaves this module:

* **An all-day row never comes back as an instant.** `order_ts` -- the sort key
  that lets an agenda interleave dates with instants -- is stripped from every
  row, and `start_kind` travels with the row so a caller cannot mistake one
  shape for the other.
* **Every response carries freshness.** Stale context may be returned, but
  never without naming its age, and `match_appointment` degrades explicitly
  rather than answering confidently from a stale index.

Calendar text -- titles, locations, attendee names -- is untrusted input. Anyone
who can send an invitation can write a meeting title, so free text is quoted
into FTS rather than passed through, and nothing here interpolates it into SQL,
a shell command, or a system instruction.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from mailctx.preflight import WAIVER_PATH

from .store import connect
from .timespec import day_bounds, zone

MAX_LIMIT = 200
_FTS_TOKEN = re.compile(r"[^\w@.\-]+", re.UNICODE)
_WORD = re.compile(r"\w+", re.UNICODE)

# Confidence bands, weakest first, so a caveat can demote by index.
BANDS = ("low", "medium", "high")


@dataclass(frozen=True)
class Freshness:
    """mailctx.query.Freshness plus window_end_ts: a calendar window is bounded
    on both sides where a mail window is not."""

    last_success_at: int | None
    age_seconds: int | None
    status: str
    error_class: str | None
    is_stale: bool
    window_start_ts: int | None
    window_end_ts: int | None

    def describe(self) -> str:
        if self.last_success_at is None:
            return "never synchronized -- results are empty or from a partial load"
        minutes = (self.age_seconds or 0) / 60
        base = f"last synced {minutes:.0f}m ago"
        if self.status != "ok":
            base += f" (last run {self.status}: {self.error_class or 'unknown'})"
        if self.is_stale:
            base += " -- STALE, may be missing recent changes"
        return base


@dataclass(frozen=True)
class Result:
    rows: list[dict[str, Any]]
    freshness: Freshness
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "freshness": {**asdict(self.freshness), "description": self.freshness.describe()},
            "truncated": self.truncated,
        }


def _fts_query(raw: str) -> str:
    """Turn free text into a safe FTS5 expression.

    Meeting titles are untrusted, so tokens are stripped of FTS operators and
    re-quoted rather than passed through. A caller cannot inject `OR`, `NEAR`,
    or a column filter this way.
    """
    tokens = [t for t in _FTS_TOKEN.split(raw or "") if t]
    if not tokens:
        return ""
    return " ".join('"' + t.replace('"', '""') + '"' for t in tokens)


class CalendarContext:
    """Read-only surface. Construct once per query batch."""

    # The timer runs every 30 minutes. mail-context calls an index stale after
    # three missed cycles; the same ratio here is 90 minutes.
    STALE_AFTER = 90 * 60

    def __init__(self, db_path: str | Path, *, now: int | None = None,
                 waiver_path: Path | None = WAIVER_PATH):
        self._conn: sqlite3.Connection = connect(db_path, read_only=True)
        self._now = now
        self._waiver_path = waiver_path

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "CalendarContext":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- freshness -------------------------------------------------------
    def _freshness(self) -> Freshness:
        row = self._conn.execute(
            """SELECT last_success_at, status, error_class, window_start_ts, window_end_ts
                 FROM cal_sync_state WHERE id = 1"""
        ).fetchone()
        now = int(time.time()) if self._now is None else self._now
        last = row["last_success_at"] if row else None
        age = (now - last) if last else None
        return Freshness(
            last_success_at=last,
            age_seconds=age,
            status=(row["status"] if row else "never_run"),
            error_class=(row["error_class"] if row else None),
            is_stale=(age is None or age > self.STALE_AFTER),
            window_start_ts=(row["window_start_ts"] if row else None),
            window_end_ts=(row["window_end_ts"] if row else None),
        )

    # -- contract --------------------------------------------------------
    def agenda(self, since: int, until: int, *, calendar_ids: list[str] | None = None,
               include_declined: bool = False, include_cancelled: bool = False,
               limit: int = 100) -> Result:
        """Occurrences between two instants, in calendar order.

        The range is applied to `order_ts`, which is the entire reason that
        column exists: it is the only way to interleave an all-day date with a
        timed instant in one ORDER BY. The rows that come back still carry
        their true shape.
        """
        limit = max(1, min(int(limit), MAX_LIMIT))
        sql = ["SELECT i.*, c.time_zone AS calendar_tz, c.summary AS calendar_summary,",
               "       e.kind AS series_kind, e.recurring_event_id",
               "  FROM cal_instances i",
               "  JOIN cal_calendars c ON c.calendar_id = i.calendar_id",
               "  LEFT JOIN cal_events e ON e.calendar_id = i.calendar_id",
               "       AND e.event_id = i.series_event_id",
               " WHERE i.order_ts >= ? AND i.order_ts < ?"]
        params: list[Any] = [int(since), int(until)]
        sql, params = self._apply_filters(sql, params, calendar_ids, include_declined,
                                          include_cancelled)
        sql.append(" ORDER BY i.order_ts ASC, i.instance_id ASC LIMIT ?")
        params.append(limit + 1)
        rows = [self._row(r) for r in self._conn.execute(" ".join(sql), params)]
        return Result(rows[:limit], self._freshness(), len(rows) > limit)

    def search(self, query: str, *, since: int | None = None, until: int | None = None,
               calendar_ids: list[str] | None = None, include_declined: bool = True,
               include_cancelled: bool = False, limit: int = 20) -> Result:
        """Full-text over occurrence titles and locations. Nothing else is
        indexed, because nothing else is stored."""
        limit = max(1, min(int(limit), MAX_LIMIT))
        match = _fts_query(query)
        head = ["SELECT i.*, c.time_zone AS calendar_tz, c.summary AS calendar_summary,",
                "       NULL AS series_kind, NULL AS recurring_event_id"]
        params: list[Any] = []
        if match:
            head += ["  FROM cal_search s",
                     "  JOIN cal_instances i ON i.rowid = s.rowid",
                     "  JOIN cal_calendars c ON c.calendar_id = i.calendar_id",
                     " WHERE cal_search MATCH ?"]
            params.append(match)
        else:
            head += ["  FROM cal_instances i",
                     "  JOIN cal_calendars c ON c.calendar_id = i.calendar_id",
                     " WHERE 1=1"]
        if since is not None:
            head.append(" AND i.order_ts >= ?")
            params.append(int(since))
        if until is not None:
            head.append(" AND i.order_ts < ?")
            params.append(int(until))
        head, params = self._apply_filters(head, params, calendar_ids, include_declined,
                                           include_cancelled)
        head.append(" ORDER BY i.order_ts DESC LIMIT ?")
        params.append(limit + 1)
        rows = [self._row(r) for r in self._conn.execute(" ".join(head), params)]
        return Result(rows[:limit], self._freshness(), len(rows) > limit)

    def event(self, event_id: str, calendar_id: str | None = None) -> Result:
        """One series-layer row with its verbatim recurrence rule, its attendees,
        and its materialized occurrences.

        The recurrence array comes back exactly as Google sent it. Nothing here
        expands it: a query must never run an RRULE engine, and this package
        does not contain one.
        """
        sql = "SELECT * FROM cal_events WHERE event_id = ?"
        params: list[Any] = [event_id]
        if calendar_id:
            sql += " AND calendar_id = ?"
            params.append(calendar_id)
        rows = []
        for row in self._conn.execute(sql, params):
            d = self._event_row(row)
            rows.append(d)
        return Result(rows, self._freshness())

    def conflicts(self, since: int, until: int, *, calendar_ids: list[str] | None = None
                  ) -> Result:
        """Pairs of timed occurrences that actually overlap.

        All-day rows are excluded on purpose: "birthday" and "10:00 standup" do
        not conflict, and pretending a date is an instant is how they would
        appear to. Transparent ("free") events and declined invitations are
        excluded because neither occupies the user.
        """
        sql = ["SELECT a.calendar_id AS a_calendar_id, a.instance_id AS a_instance_id,",
               "       a.summary AS a_summary, a.start_utc AS a_start_utc,",
               "       a.end_utc AS a_end_utc, a.start_tz AS a_start_tz,",
               "       b.calendar_id AS b_calendar_id, b.instance_id AS b_instance_id,",
               "       b.summary AS b_summary, b.start_utc AS b_start_utc,",
               "       b.end_utc AS b_end_utc, b.start_tz AS b_start_tz",
               "  FROM cal_instances a JOIN cal_instances b",
               "    ON a.rowid < b.rowid",
               "   AND a.start_utc < b.end_utc AND b.start_utc < a.end_utc",
               " WHERE a.start_kind = 'timed' AND b.start_kind = 'timed'",
               "   AND a.status != 'cancelled' AND b.status != 'cancelled'",
               "   AND COALESCE(a.transparency,'opaque') = 'opaque'",
               "   AND COALESCE(b.transparency,'opaque') = 'opaque'",
               "   AND COALESCE(a.self_response_status,'') != 'declined'",
               "   AND COALESCE(b.self_response_status,'') != 'declined'",
               "   AND a.order_ts >= ? AND a.order_ts < ?"]
        params: list[Any] = [int(since), int(until)]
        if calendar_ids:
            marks = ",".join("?" * len(calendar_ids))
            sql.append(f"   AND a.calendar_id IN ({marks}) AND b.calendar_id IN ({marks})")
            params.extend(calendar_ids)
            params.extend(calendar_ids)
        sql.append(" ORDER BY a.start_utc ASC LIMIT ?")
        params.append(MAX_LIMIT)
        rows = []
        for r in self._conn.execute(" ".join(sql), params):
            d = dict(r)
            d["overlap_seconds"] = min(d["a_end_utc"], d["b_end_utc"]) - max(
                d["a_start_utc"], d["b_start_utc"])
            rows.append(d)
        return Result(rows, self._freshness())

    def match_appointment(self, *, start: int | None = None, end: int | None = None,
                          all_day_date: str | None = None, title_hint: str | None = None,
                          tolerance_minutes: int = 60, limit: int = 10) -> Result:
        """Is something like this already on the calendar?

        The appointment-deduplication check mail-context will consult before
        proposing an `action_candidates` row with `category='appointment'`. It
        is read-only and side-effect free: it answers a question, marks nothing,
        and writes to neither database.

        It returns **evidence, not a verdict**. Each row carries a match reason,
        a confidence band, the signed time delta, and a list of caveats. The
        decision to suppress a candidate belongs to the caller, because only the
        caller knows what it is about to propose.

        Caveats that matter:

        * `cancelled` -- a cancelled occurrence must not count as scheduled;
        * `declined`  -- neither must an invitation the user declined;
        * `stale_index` -- when the index is stale every band is capped at
          `low`, because a confident "already scheduled" from an old index is
          exactly how a real appointment gets silently dropped.

        Takes either a timed `start` (with optional `end`) or an `all_day_date`,
        never a blend of the two -- the same rule the schema enforces.
        """
        if (start is None) == (all_day_date is None):
            raise ValueError("pass exactly one of start= (timed) or all_day_date= (all-day)")
        limit = max(1, min(int(limit), MAX_LIMIT))
        fresh = self._freshness()
        tolerance = max(0, int(tolerance_minutes)) * 60

        if start is not None:
            rows = self._match_timed(int(start), end, tolerance, limit)
        else:
            rows = self._match_all_day(str(all_day_date), limit)

        hint_tokens = {t.lower() for t in _WORD.findall(title_hint or "")}
        out = []
        for row, reason, band, delta in rows:
            caveats: list[str] = []
            if row["status"] == "cancelled":
                caveats.append("cancelled")
            if row["status"] == "tentative":
                caveats.append("tentative")
            if (row.get("self_response_status") or "") == "declined":
                caveats.append("declined")
            if hint_tokens:
                title = {t.lower() for t in _WORD.findall(row.get("summary") or "")}
                if hint_tokens & title:
                    caveats.append("title_matched")
                else:
                    caveats.append("title_mismatch")
                    band = BANDS[max(0, BANDS.index(band) - 1)]
            if "cancelled" in caveats or "declined" in caveats:
                band = "low"
            if fresh.is_stale:
                caveats.append("stale_index")
                band = "low"
            out.append({**row, "match_reason": reason, "confidence": band,
                        "delta_seconds": delta, "caveats": caveats})
        return Result(out, fresh)

    def status(self) -> Result:
        """Counts, the last run, the window, and the preflight waiver.

        The waiver is surfaced here on purpose. An operator may waive the
        encryption-at-rest gate only by writing an explicit file, and the plan's
        condition for that waiver being acceptable is that it appears in every
        status response rather than being quietly forgotten.
        """
        counts = self._conn.execute(
            """SELECT (SELECT COUNT(*) FROM cal_calendars WHERE selected = 1
                                                            AND deleted_at IS NULL) AS calendars,
                      (SELECT COUNT(*) FROM cal_events WHERE deleted_at IS NULL)     AS events,
                      (SELECT COUNT(*) FROM cal_events WHERE kind = 'series'
                                                         AND deleted_at IS NULL)     AS series,
                      (SELECT COUNT(*) FROM cal_instances)                           AS instances,
                      (SELECT COUNT(*) FROM cal_instances WHERE status = 'cancelled') AS cancelled"""
        ).fetchone()
        last_run = self._conn.execute(
            """SELECT kind, started_at, finished_at, added, updated, deleted, pruned,
                      instances, status, error_class
                 FROM cal_sync_runs ORDER BY started_at DESC LIMIT 1"""
        ).fetchone()
        row = dict(counts)
        row["last_run"] = dict(last_run) if last_run else None
        row["calendars_detail"] = [
            {"calendar_ref": r["calendar_id"], "status": r["status"],
             "error_class": r["error_class"], "anchor_start_ts": r["anchor_start_ts"],
             "anchor_end_ts": r["anchor_end_ts"], "has_token": r["sync_token"] is not None}
            for r in self._conn.execute(
                "SELECT s.*, c.summary FROM cal_calendar_sync s "
                "JOIN cal_calendars c ON c.calendar_id = s.calendar_id ORDER BY s.calendar_id")
        ]
        row["encryption_waiver"] = self._waiver()
        row["write_path"] = "none"   # not a boast: asserted by tests/test_no_write_path.py
        return Result([row], self._freshness())

    # -- helpers ---------------------------------------------------------
    def _waiver(self) -> dict[str, Any]:
        path = self._waiver_path
        if not path:
            return {"active": False}
        try:
            first = path.read_text().strip().splitlines()
        except OSError:
            return {"active": False}
        return {"active": True, "path": str(path),
                "reason": (first[0][:200] if first else "no reason recorded"),
                "note": "index is unencrypted at rest by explicit operator decision"}

    def _apply_filters(self, sql: list[str], params: list[Any],
                       calendar_ids: list[str] | None, include_declined: bool,
                       include_cancelled: bool) -> tuple[list[str], list[Any]]:
        if calendar_ids:
            marks = ",".join("?" * len(calendar_ids))
            sql.append(f" AND i.calendar_id IN ({marks})")
            params.extend(calendar_ids)
        if not include_cancelled:
            sql.append(" AND i.status != 'cancelled'")
        if not include_declined:
            sql.append(" AND COALESCE(i.self_response_status,'') != 'declined'")
        return sql, params

    def _match_timed(self, start: int, end: int | None, tolerance: int,
                     limit: int) -> list[tuple[dict, str, str, int | None]]:
        end = int(end) if end else start
        rows = self._conn.execute(
            """SELECT i.*, c.time_zone AS calendar_tz, c.summary AS calendar_summary,
                      NULL AS series_kind, NULL AS recurring_event_id
                 FROM cal_instances i
                 JOIN cal_calendars c ON c.calendar_id = i.calendar_id
                WHERE (i.start_kind = 'timed'
                       AND (ABS(i.start_utc - ?) <= ?
                            OR (i.start_utc < ? AND i.end_utc > ?)))
                   OR (i.start_kind = 'all_day' AND i.order_ts <= ? AND ? < i.order_ts + 86400)
                ORDER BY ABS(COALESCE(i.start_utc, i.order_ts) - ?) ASC
                LIMIT ?""",
            (start, tolerance, end, start, start, start, start, limit),
        ).fetchall()
        out = []
        for r in rows:
            row = self._row(r)
            if row["start_kind"] == "all_day":
                out.append((row, "same_day", "medium", None))
                continue
            delta = int(r["start_utc"]) - start
            if delta == 0:
                out.append((row, "exact_start", "high", 0))
            elif abs(delta) <= min(tolerance, 15 * 60):
                out.append((row, "within_tolerance", "high", delta))
            elif abs(delta) <= tolerance:
                out.append((row, "within_tolerance", "medium", delta))
            else:
                out.append((row, "overlap", "medium", delta))
        return out

    def _match_all_day(self, day: str, limit: int) -> list[tuple[dict, str, str, int | None]]:
        rows = self._conn.execute(
            """SELECT i.*, c.time_zone AS calendar_tz, c.summary AS calendar_summary,
                      NULL AS series_kind, NULL AS recurring_event_id
                 FROM cal_instances i
                 JOIN cal_calendars c ON c.calendar_id = i.calendar_id
                WHERE i.start_date = ?
                   OR (i.start_kind = 'timed' AND i.order_ts >= ? AND i.order_ts < ?)
                ORDER BY i.order_ts ASC LIMIT ?""",
            (day, *day_bounds(day, calendar_tz=self._primary_tz()), limit),
        ).fetchall()
        out = []
        for r in rows:
            row = self._row(r)
            reason = "same_date" if row["start_kind"] == "all_day" else "same_day"
            band = "high" if row["start_kind"] == "all_day" else "medium"
            out.append((row, reason, band, None))
        return out

    def _primary_tz(self) -> str:
        row = self._conn.execute(
            "SELECT time_zone FROM cal_calendars WHERE is_primary = 1 LIMIT 1").fetchone()
        if row and row["time_zone"]:
            return row["time_zone"]
        row = self._conn.execute(
            "SELECT time_zone FROM cal_calendars LIMIT 1").fetchone()
        return (row["time_zone"] if row and row["time_zone"] else "UTC")

    def _row(self, r: sqlite3.Row) -> dict[str, Any]:
        """One occurrence, shaped so its kind cannot be misread.

        `order_ts` is removed here and nowhere else matters: it is local
        midnight for an all-day row, and returning it would hand a caller an
        instant that a birthday never had.
        """
        d = dict(r)
        d.pop("order_ts", None)
        d.pop("rowid", None)
        kind = d.get("start_kind")
        tz = d.get("calendar_tz") or "UTC"
        if kind == "timed":
            d.pop("start_date", None)
            d.pop("end_date", None)
            d["start_local"] = _local(d["start_utc"], d.get("start_tz") or tz)
            d["end_local"] = _local(d["end_utc"], d.get("end_tz") or d.get("start_tz") or tz)
        else:
            for key in ("start_utc", "start_tz", "end_utc", "end_tz"):
                d.pop(key, None)
        d["is_cancelled"] = d.get("status") == "cancelled"
        d["is_declined"] = (d.get("self_response_status") or "") == "declined"
        return d

    def _event_row(self, r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d.pop("materialized_etag", None)
        d.pop("materialized_at", None)
        d["recurrence"] = json.loads(d["recurrence"]) if d["recurrence"] else []
        d["attendees"] = [
            {"email": a["email"], "response_status": a["response_status"],
             "is_self": bool(a["is_self"]), "optional": bool(a["optional"])}
            for a in self._conn.execute(
                "SELECT * FROM cal_attendees WHERE calendar_id = ? AND event_id = ? ORDER BY email",
                (d["calendar_id"], d["event_id"]))
        ]
        d["instances"] = [
            self._row(x) for x in self._conn.execute(
                """SELECT i.*, c.time_zone AS calendar_tz, c.summary AS calendar_summary
                     FROM cal_instances i JOIN cal_calendars c ON c.calendar_id = i.calendar_id
                    WHERE i.calendar_id = ? AND i.series_event_id = ?
                    ORDER BY i.order_ts ASC""",
                (d["calendar_id"], d["event_id"]))
        ]
        return d


def _local(ts: int | None, tz: str) -> str | None:
    """An instant rendered in its original zone, for display only."""
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts), zone(tz)).isoformat()
