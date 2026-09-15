"""Synchronization: bounded initial load per calendar, then incremental tokens.

Four invariants carry the whole design.

* **The token is stored last.** Google returns `nextSyncToken` only on the final
  page. It is written in its own transaction *after* the data transaction for
  that page has committed, so a crash in between leaves the previous token in
  place and the next run replays -- which is safe precisely because every write
  is idempotent. Reverse the order and a crash silently drops a page.

* **The anchor is captured first.** The window used for a full listing is
  computed *before* the listing starts and stored alongside the token it minted.
  `events.list` forbids `timeMin`/`timeMax` together with `syncToken`, so the
  token silently carries the bounds of its originating request; recomputing the
  anchor afterwards would record bounds the request never used and mark the far
  edge of the window as covered when nothing there was ever fetched.

* **Resume is for the initial load only.** Skipping a series whose etag matches
  what we last expanded makes a long first load resumable. On an incremental
  run the same skip is a bug: the change we were just told about is exactly the
  reason the stored expansion is stale -- and an override or cancellation can
  arrive with the *master's* etag unchanged.

* **Per-calendar failure is isolated.** One calendar returning 403 or 404 must
  not abort the run for the others, and must not disturb their cursors.

Expansion of recurrence rules is Google's job, not ours; see `gcal.instances`.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Sequence

from . import logs
from .gcal import CalendarReadOnly, TransportError
from .store import Calendar, Event, Instance, Store
from .timespec import (
    ANCHOR_DRIFT_LIMIT_SECONDS,
    WINDOW_BACK_MONTHS,
    WINDOW_FORWARD_MONTHS,
    TimeSpec,
    TimeSpecError,
    date_range,
    order_ts,
    parse_time,
    rfc3339,
    window_bounds,
)


@dataclass
class CalendarResult:
    calendar_id: str
    mode: str                       # 'initial' | 'incremental' | 'resync'
    added: int = 0
    updated: int = 0
    deleted: int = 0
    instances: int = 0
    series: int = 0
    skipped: int = 0
    pages: int = 0
    status: str = "ok"
    error_class: str | None = None


@dataclass
class SyncResult:
    kind: str
    calendars: int = 0
    added: int = 0
    updated: int = 0
    deleted: int = 0
    pruned: int = 0
    instances: int = 0
    series: int = 0
    skipped: int = 0
    pages: int = 0
    resynced: bool = False
    status: str = "ok"
    error_class: str | None = None
    elapsed: float = 0.0
    per_calendar: list[CalendarResult] = field(default_factory=list)

    def summary(self) -> str:
        return (f"{self.kind}: calendars={self.calendars} +{self.added} ~{self.updated} "
                f"-{self.deleted} instances={self.instances} pruned={self.pruned} "
                f"pages={self.pages} in {self.elapsed:.1f}s "
                f"[{self.status}{'/' + self.error_class if self.error_class else ''}]")


# -- parsing --------------------------------------------------------------

def parse_calendar(raw: dict) -> Calendar:
    return Calendar(
        calendar_id=raw["id"],
        summary=raw.get("summary", "") or raw.get("summaryOverride", ""),
        time_zone=raw.get("timeZone", "UTC") or "UTC",
        selected=bool(raw.get("selected", False)),
        is_primary=bool(raw.get("primary", False)),
        access_role=raw.get("accessRole", ""),
    )


def _attendees(raw: dict) -> tuple[tuple[str, str, bool, bool], ...]:
    out = []
    for a in raw.get("attendees", []) or []:
        email = a.get("email")
        if not email:
            continue
        out.append((email, a.get("responseStatus", "needsAction"),
                    bool(a.get("self")), bool(a.get("optional"))))
    return tuple(out)


def _self_response(raw: dict) -> str | None:
    for a in raw.get("attendees", []) or []:
        if a.get("self"):
            return a.get("responseStatus")
    return None


def parse_event(raw: dict, *, calendar_id: str, calendar_tz: str) -> Event | None:
    """One events.list item -> Event, or None when it is a plain deletion.

    A cancelled *occurrence* of a series arrives as its own event carrying
    `recurringEventId` and `originalStartTime` but no `start`/`end`. That is a
    row we must keep -- "this occurrence was cancelled" and "this occurrence
    never existed" answer "am I free Thursday?" differently -- so its original
    start doubles as its stored time, and its status records what it is.

    A cancelled event with neither is a deletion; the caller tombstones it.
    """
    status = raw.get("status", "confirmed")
    if status not in ("confirmed", "tentative", "cancelled"):
        status = "confirmed"

    start = parse_time(raw.get("start"), calendar_tz=calendar_tz)
    end = parse_time(raw.get("end"), calendar_tz=calendar_tz)
    original = parse_time(raw.get("originalStartTime"), calendar_tz=calendar_tz)

    if start is None:
        if original is None:
            return None            # a deletion, not an occurrence
        start = original
    if end is None:
        end = start

    if start.kind == "all_day" and end.kind == "all_day":
        # Google's all-day end.date is exclusive; ours is inclusive.
        s, e = date_range(str(start.date), str(end.date))
        end = TimeSpec("all_day", date=e)
    elif start.kind != end.kind:
        # Mixed shapes are not something to reconcile by guessing.
        end = start

    recurrence = tuple(raw.get("recurrence") or ())
    if recurrence:
        kind = "series"
    elif raw.get("recurringEventId"):
        kind = "override"
    else:
        kind = "single"

    updated = raw.get("updated")
    updated_ts = None
    if updated:
        try:
            spec = parse_time({"dateTime": updated}, calendar_tz="UTC")
            updated_ts = spec.utc if spec else None
        except TimeSpecError:
            updated_ts = None

    return Event(
        calendar_id=calendar_id,
        event_id=raw["id"],
        kind=kind,
        start=start,
        end=end,
        status=status,
        summary=raw.get("summary", "") or "",
        location=raw.get("location", "") or "",
        ical_uid=raw.get("iCalUID", "") or "",
        etag=raw.get("etag", "") or "",
        sequence=raw.get("sequence"),
        recurring_event_id=raw.get("recurringEventId"),
        original_start=original,
        organizer_email=(raw.get("organizer") or {}).get("email", ""),
        creator_email=(raw.get("creator") or {}).get("email", ""),
        self_response_status=_self_response(raw),
        transparency=raw.get("transparency", "opaque") or "opaque",
        visibility=raw.get("visibility", "default") or "default",
        recurrence=recurrence,
        updated_ts=updated_ts,
        attendees=_attendees(raw),
    )


# -- the syncer -----------------------------------------------------------

class Syncer:
    def __init__(self, store: Store, api: CalendarReadOnly, *,
                 back_months: int = WINDOW_BACK_MONTHS,
                 forward_months: int = WINDOW_FORWARD_MONTHS,
                 concurrency: int = 8,
                 resume: bool = True,
                 drift_limit: int = ANCHOR_DRIFT_LIMIT_SECONDS,
                 on_progress: Callable[[str], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 now: int | None = None):
        """concurrency exists because events.instances is latency-bound, not
        quota-bound: one GET per recurring series adds up on a calendar with a
        few hundred of them. Workers fetch; the calling thread writes, because
        the SQLite connection is not thread-safe. `AccessTokenProvider.bearer()`
        already takes a lock, so a token refresh under concurrency is safe."""
        self.store = store
        self.api = api
        self.back_months = back_months
        self.forward_months = forward_months
        self.concurrency = max(1, concurrency)
        self.resume = resume
        self.drift_limit = drift_limit
        self._progress = on_progress or (lambda _msg: None)
        self._sleep = sleep
        self._now = now

    # -- helpers ---------------------------------------------------------
    def now(self) -> int:
        return int(time.time()) if self._now is None else self._now

    def window(self) -> tuple[int, int]:
        return window_bounds(self.now(), back_months=self.back_months,
                             forward_months=self.forward_months)

    def _drift(self, cursor, window: tuple[int, int]) -> int:
        """How far the desired window has slid from the token's fixed anchor."""
        if cursor is None or cursor["anchor_start_ts"] is None:
            return 0
        return max(abs(window[0] - cursor["anchor_start_ts"]),
                   abs(window[1] - cursor["anchor_end_ts"]))

    # -- calendar list ---------------------------------------------------
    def sync_calendar_list(self) -> int:
        """Index only entries the user marked `selected` in their own Calendar
        UI. Their existing choice is the scope control; inventing a second one
        would mean two places to look when a calendar is unexpectedly missing."""
        raws = list(self.api.paginate(self.api.calendar_list, "items"))
        seen = set()
        self.store.begin()
        try:
            for raw in raws:
                cal = parse_calendar(raw)
                seen.add(cal.calendar_id)
                if raw.get("deleted"):
                    self.store.tombstone_calendar(cal.calendar_id, now=self.now())
                    continue
                self.store.upsert_calendar(cal, now=self.now())
            for row in self.store.conn.execute(
                    "SELECT calendar_id FROM cal_calendars WHERE deleted_at IS NULL"):
                if row["calendar_id"] not in seen:
                    # Vanished from calendarList entirely: unsubscribed or revoked.
                    self.store.tombstone_calendar(row["calendar_id"], now=self.now())
            self.store.commit()
        except Exception:
            self.store.abort()
            raise
        return len(seen)

    # -- one calendar ----------------------------------------------------
    def sync_calendar(self, cal_row, window: tuple[int, int]) -> CalendarResult:
        calendar_id = cal_row["calendar_id"]
        tz = cal_row["time_zone"] or "UTC"
        cursor = self.store.calendar_cursor(calendar_id)
        token = cursor["sync_token"] if cursor else None
        drift = self._drift(cursor, window)

        if token and drift <= self.drift_limit:
            mode = "incremental"
        elif token:
            # A rolling window over a fixed anchor goes quietly wrong: the token
            # keeps delivering changes for the window as it stood a month ago,
            # and newly-in-range events at the far edge never arrive. Re-anchor.
            mode, token = "resync", None
            logs.log("window_reanchor", calendar_ref=logs.calendar_ref(calendar_id),
                     drift_seconds=drift)
        else:
            mode = "initial"

        try:
            return self._run_calendar(calendar_id, tz, window, mode, token)
        except TransportError as exc:
            if exc.error_class == "gone" and mode == "incremental":
                # Expired syncToken. Documented recovery: discard it and run a
                # bounded full resync for this calendar rather than failing.
                self.store.abort()
                self.store.begin()
                self.store.clear_calendar_token(calendar_id)
                self.store.commit()
                logs.log("sync_token_expired", calendar_ref=logs.calendar_ref(calendar_id),
                         http_status=exc.status or 410)
                res = self._run_calendar(calendar_id, tz, window, "resync", None)
                res.mode = "resync"
                return res
            raise

    def _run_calendar(self, calendar_id: str, tz: str, window: tuple[int, int],
                      mode: str, token: str | None) -> CalendarResult:
        res = CalendarResult(calendar_id=calendar_id, mode=mode)
        full = mode in ("initial", "resync")
        # Captured BEFORE the listing. See the module docstring.
        anchor_start, anchor_end = window
        changed: set[str] = set()
        next_token: str | None = None
        page_token: str | None = None

        while True:
            if full:
                page = self.api.list_events(
                    calendar_id, time_min=rfc3339(anchor_start),
                    time_max=rfc3339(anchor_end), page_token=page_token)
            else:
                page = self.api.list_events(calendar_id, sync_token=token,
                                            page_token=page_token)
            res.pages += 1
            items = page.get("items", []) or []

            self.store.begin()
            try:
                for raw in items:
                    self._apply_event(raw, calendar_id, tz, res, changed)
                self.store.commit()
            except Exception:
                self.store.abort()
                raise

            next_token = page.get("nextSyncToken") or next_token
            page_token = page.get("nextPageToken")
            self._progress(f"{calendar_id[:24]}: page {res.pages}, {res.added + res.updated} events")
            if not page_token:
                break

        # Re-materialize only after the series layer for this calendar is fully
        # applied, never interleaved with it: an override can arrive on a later
        # page than its master, and expanding in between would store an
        # occurrence that the very next page contradicts.
        if full:
            targets = {r["event_id"] for r in self.store.live_series(calendar_id)}
        else:
            targets = self._resolve_targets(calendar_id, changed)
        self._materialize(calendar_id, tz, targets, window, res, initial=(mode == "initial"))

        # Token last, in its own transaction, after the data has committed.
        # On an incremental run the anchor stays as it was: the new token still
        # descends from the bounds of the full sync that originally minted one.
        if next_token:
            stored = self.store.calendar_cursor(calendar_id)
            if not full and stored and stored["anchor_start_ts"] is not None:
                anchor_start, anchor_end = stored["anchor_start_ts"], stored["anchor_end_ts"]
            self.store.begin()
            self.store.advance_calendar_cursor(
                calendar_id, next_token, anchor_start_ts=anchor_start,
                anchor_end_ts=anchor_end, now=self.now())
            self.store.commit()
        return res

    def _apply_event(self, raw: dict, calendar_id: str, tz: str,
                     res: CalendarResult, changed: set[str]) -> None:
        try:
            ev = parse_event(raw, calendar_id=calendar_id, calendar_tz=tz)
        except TimeSpecError:
            # One malformed event must not abort a calendar. Recorded as a
            # count, never with its content.
            logs.log("event_unparseable", calendar_ref=logs.calendar_ref(calendar_id),
                     event_id=raw.get("id", "?"))
            return

        if ev is None:
            if self.store.tombstone_event(calendar_id, raw["id"], now=self.now()):
                res.deleted += 1
            changed.add(raw["id"])
            return

        if self.store.upsert_event(ev, now=self.now()) == "added":
            res.added += 1
        else:
            res.updated += 1
        changed.add(ev.event_id)
        if ev.recurring_event_id:
            changed.add(ev.recurring_event_id)

    def _resolve_targets(self, calendar_id: str, changed: set[str]) -> set[str]:
        """Map changed ids onto the series that must be re-expanded.

        An override is not expanded on its own -- it has no occurrences of its
        own -- so a changed override redirects to its master.
        """
        targets: set[str] = set()
        for event_id in changed:
            row = self.store.event_row(calendar_id, event_id)
            if row is None:
                continue
            if row["kind"] == "override" and row["recurring_event_id"]:
                targets.add(row["recurring_event_id"])
            else:
                targets.add(event_id)
        return targets

    # -- materialization -------------------------------------------------
    def _materialize(self, calendar_id: str, tz: str, targets: set[str],
                     window: tuple[int, int], res: CalendarResult, *, initial: bool) -> None:
        rows = []
        for event_id in sorted(targets):
            row = self.store.event_row(calendar_id, event_id)
            if row is None or row["deleted_at"] is not None:
                continue
            if row["kind"] == "override":
                continue
            if initial and self.resume:
                etag, count = self.store.materialized_state(calendar_id, event_id)
                if etag and etag == row["etag"] and count:
                    res.skipped += 1
                    continue
            rows.append(row)

        singles = [r for r in rows if not r["recurrence"]]
        recurring = [r for r in rows if r["recurrence"]]

        # Non-recurring events get exactly one instance derived locally. No API
        # call: asking Google to expand a series of one is pure latency.
        for row in singles:
            self._store_instances(calendar_id, row, [self._single_instance(row, tz)], res)

        if not recurring:
            return
        if self.concurrency == 1:
            for row in recurring:
                self._store_instances(calendar_id, row,
                                      self._expand(calendar_id, row, tz, window), res)
            return
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            fetched = pool.map(lambda r: (r, self._fetch_instances(calendar_id, r, window)),
                               recurring)
            # Writes stay on this thread; workers never touch the connection.
            for row, raws in fetched:
                self._store_instances(
                    calendar_id, row,
                    [i for i in (self._instance_from(raw, row, calendar_id, tz) for raw in raws)
                     if i is not None],
                    res)

    def _fetch_instances(self, calendar_id: str, row, window: tuple[int, int]) -> list[dict]:
        for attempt in range(4):
            try:
                return list(self.api.paginate(
                    self.api.instances, "items", calendar_id=calendar_id,
                    event_id=row["event_id"], time_min=rfc3339(window[0]),
                    time_max=rfc3339(window[1])))
            except TransportError as exc:
                if exc.error_class == "not_found":
                    return []      # deleted between listing and expansion
                if exc.error_class == "rate_limited":
                    self._sleep(2.0 * (2 ** attempt))
                    continue
                raise
        raise TransportError("rate_limited_persistent")

    def _expand(self, calendar_id: str, row, tz: str, window: tuple[int, int]) -> list[Instance]:
        raws = self._fetch_instances(calendar_id, row, window)
        out = [self._instance_from(raw, row, calendar_id, tz) for raw in raws]
        return [i for i in out if i is not None]

    def _store_instances(self, calendar_id: str, row, instances: Sequence[Instance],
                         res: CalendarResult) -> None:
        self.store.begin()
        try:
            res.instances += self.store.replace_instances(
                calendar_id, row["event_id"], instances, now=self.now())
            self.store.mark_materialized(calendar_id, row["event_id"], row["etag"],
                                         now=self.now())
            self.store.commit()
        except Exception:
            self.store.abort()
            raise
        res.series += 1

    def _single_instance(self, row, tz: str) -> Instance:
        start = _spec_from_row(row, "start")
        end = _spec_from_row(row, "end")
        return Instance(
            calendar_id=row["calendar_id"],
            instance_id=row["event_id"],
            series_event_id=row["event_id"],
            start=start,
            end=end,
            order_ts=order_ts(start, calendar_tz=tz),
            status=row["status"],
            is_override=False,
            summary=row["summary"],
            location=row["location"],
            transparency=row["transparency"],
            self_response_status=row["self_response_status"],
        )

    def _instance_from(self, raw: dict, series_row, calendar_id: str, tz: str) -> Instance | None:
        """One expanded occurrence, with overrides denormalized onto the row.

        A cancelled occurrence arrives without start/end -- only
        `originalStartTime` -- so that becomes its time. Its end is derived from
        the master's duration, which is the honest answer to "when would this
        have been"; the row is flagged cancelled so nothing treats it as busy.
        """
        status = raw.get("status", "confirmed")
        if status not in ("confirmed", "tentative", "cancelled"):
            status = "confirmed"
        try:
            start = parse_time(raw.get("start"), calendar_tz=tz)
            end = parse_time(raw.get("end"), calendar_tz=tz)
            original = parse_time(raw.get("originalStartTime"), calendar_tz=tz)
        except TimeSpecError:
            return None
        if start is None:
            start = original
        if start is None:
            return None
        if end is None:
            end = _derive_end(start, series_row)
        if start.kind == "all_day" and end.kind == "all_day" and raw.get("end"):
            _, inclusive = date_range(str(start.date), str(end.date))
            end = TimeSpec("all_day", date=inclusive)
        if start.kind != end.kind:
            end = start

        instance_id = raw.get("id") or f"{series_row['event_id']}_{start.utc or start.date}"
        override_row = self.store.event_row(calendar_id, instance_id)
        is_override = (status == "cancelled"
                       or bool(override_row and override_row["kind"] == "override"))

        return Instance(
            calendar_id=calendar_id,
            instance_id=instance_id,
            series_event_id=series_row["event_id"],
            start=start,
            end=end,
            order_ts=order_ts(start, calendar_tz=tz),
            status=status,
            is_override=is_override,
            summary=raw.get("summary", series_row["summary"]) or "",
            location=raw.get("location", series_row["location"]) or "",
            transparency=raw.get("transparency", series_row["transparency"]) or "opaque",
            self_response_status=_self_response(raw) or series_row["self_response_status"],
        )

    # -- entry point -----------------------------------------------------
    def sync(self) -> SyncResult:
        """One run: calendar list, then every selected calendar, then prune.

        This is what the 30-minute timer calls. It makes no model call.
        """
        t0 = time.time()
        window = self.window()
        any_token = self.store.conn.execute(
            "SELECT COUNT(*) FROM cal_calendar_sync WHERE sync_token IS NOT NULL").fetchone()[0]
        res = SyncResult(kind="incremental" if any_token else "initial")
        run_id = self.store.start_run(res.kind, now=self.now())

        try:
            res.calendars = self.sync_calendar_list()
        except TransportError as exc:
            res.status, res.error_class = "error", exc.error_class
            self.store.abort()
            self.store.begin()
            self.store.record_failure(exc.error_class)
            self.store.commit()
            self.store.finish_run(run_id, status="error", error_class=exc.error_class,
                                  now=self.now())
            res.elapsed = time.time() - t0
            return res

        for cal_row in self.store.selected_calendars():
            calendar_id = cal_row["calendar_id"]
            try:
                cres = self.sync_calendar(cal_row, window)
            except TransportError as exc:
                # Isolation: this calendar's failure changes nothing for the rest,
                # and leaves this one's last good cursor and data untouched.
                self.store.abort()
                self.store.begin()
                self.store.record_calendar_failure(calendar_id, exc.error_class)
                self.store.commit()
                logs.log("calendar_failed", calendar_ref=logs.calendar_ref(calendar_id),
                         error_class=exc.error_class)
                res.per_calendar.append(CalendarResult(
                    calendar_id=calendar_id, mode="incremental", status="error",
                    error_class=exc.error_class))
                continue
            res.per_calendar.append(cres)
            res.added += cres.added
            res.updated += cres.updated
            res.deleted += cres.deleted
            res.instances += cres.instances
            res.series += cres.series
            res.skipped += cres.skipped
            res.pages += cres.pages
            if cres.mode == "resync":
                res.resynced = True

        self.store.begin()
        res.pruned = self.store.prune(window_start_ts=window[0], window_end_ts=window[1],
                                      now=self.now())
        self.store.commit()

        failed = [c for c in res.per_calendar if c.status != "ok"]
        if failed and len(failed) == len(res.per_calendar) and res.per_calendar:
            # Every calendar failed: this is not a successful run, and saying so
            # keeps a dead grant from reading as a merely empty calendar.
            res.status = "error"
            res.error_class = failed[0].error_class
            self.store.begin()
            self.store.record_failure(res.error_class or "unknown")
            self.store.commit()
        else:
            self.store.begin()
            self.store.advance_global(window_start_ts=window[0], window_end_ts=window[1],
                                      kind=("initial" if res.kind == "initial" else
                                            ("resync" if res.resynced else "incremental")),
                                      now=self.now())
            self.store.commit()
            if failed:
                res.status = "partial"
                res.error_class = failed[0].error_class

        res.elapsed = time.time() - t0
        self.store.finish_run(run_id, status=res.status, added=res.added, updated=res.updated,
                              deleted=res.deleted, pruned=res.pruned, instances=res.instances,
                              error_class=res.error_class, now=self.now())
        logs.log("sync_done", kind=res.kind, status=res.status, added=res.added,
                 updated=res.updated, deleted=res.deleted, pruned=res.pruned,
                 instances=res.instances, series=res.series, skipped=res.skipped,
                 pages=res.pages, window_start_ts=window[0], window_end_ts=window[1],
                 elapsed_ms=int(res.elapsed * 1000))
        return res


def _spec_from_row(row, prefix: str) -> TimeSpec:
    if row["start_kind"] == "timed":
        return TimeSpec("timed", utc=row[f"{prefix}_utc"], tz=row[f"{prefix}_tz"])
    return TimeSpec("all_day", date=row[f"{prefix}_date"])


def _derive_end(start: TimeSpec, series_row) -> TimeSpec:
    """The master's duration applied to an occurrence that arrived without one."""
    if start.kind == "timed":
        if series_row["start_utc"] is not None and series_row["end_utc"] is not None:
            span = int(series_row["end_utc"]) - int(series_row["start_utc"])
            return TimeSpec("timed", utc=int(start.utc or 0) + max(0, span), tz=start.tz)
        return TimeSpec("timed", utc=int(start.utc or 0), tz=start.tz)
    if series_row["start_date"] and series_row["end_date"]:
        span = (date.fromisoformat(series_row["end_date"])
                - date.fromisoformat(series_row["start_date"])).days
        end = date.fromisoformat(str(start.date)) + timedelta(days=max(0, span))
        return TimeSpec("all_day", date=end.isoformat())
    return TimeSpec("all_day", date=start.date)


def recurrence_of(row) -> list[str]:
    """The stored RRULE array, exactly as Google sent it. Read-side helper so a
    caller can audit the rule without this package ever interpreting one."""
    return json.loads(row["recurrence"]) if row["recurrence"] else []
