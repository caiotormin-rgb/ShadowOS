"""Synthetic calendars only. Real calendar data must never enter this repository.

Every timestamp below is derived from the window constants rather than from a
fixed epoch. A hardcoded epoch drifts out of a rolling -3/+12-month window as
real time passes, and the resulting prune then looks exactly like a bug in the
code rather than a bug in the fixture.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone

from calctx.timespec import window_bounds

NOW = int(time.time())
WINDOW_START, WINDOW_END = window_bounds(NOW)

# Anchored to the window, not to a day count, so these stay valid forever.
PAST = NOW - (NOW - WINDOW_START) // 2          # comfortably inside the back edge
SOON = NOW + (WINDOW_END - NOW) // 8            # near-future, inside the window
LATER = NOW + (WINDOW_END - NOW) // 2           # far-future, still inside
BEYOND = WINDOW_END + 86400                     # past the forward edge by construction
ANCIENT = WINDOW_START - 86400                  # before the back edge by construction

CAL_ID = "primary@example.invalid"
OTHER_CAL_ID = "team@example.invalid"


def iso(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), timezone.utc).isoformat()


def day_of(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d")


def memory_conn():
    """A connection configured exactly like calctx.store.connect().

    isolation_level=None matters: with Python's default, sqlite3 opens implicit
    transactions and an explicit BEGIN then fails. Tests that do not mirror
    production connection settings test the wrong thing.
    """
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# -- raw Google payloads --------------------------------------------------

def raw_calendar(calendar_id: str = CAL_ID, *, tz: str = "UTC", selected: bool = True,
                 primary: bool = True, summary: str = "Personal",
                 access_role: str = "owner", deleted: bool = False) -> dict:
    out = {"id": calendar_id, "summary": summary, "timeZone": tz,
           "selected": selected, "accessRole": access_role}
    if primary:
        out["primary"] = True
    if deleted:
        out["deleted"] = True
    return out


def raw_timed(event_id: str, *, start: int = SOON, duration: int = 3600,
              summary: str = "Standup", location: str = "", tz: str = "UTC",
              status: str = "confirmed", etag: str = '"v1"',
              transparency: str = "opaque", attendees=(), recurrence=None,
              recurring_event_id: str | None = None,
              original_start: int | None = None) -> dict:
    out: dict = {
        "id": event_id,
        "etag": etag,
        "status": status,
        "summary": summary,
        "location": location,
        "iCalUID": f"{event_id}@google.com",
        "sequence": 0,
        "start": {"dateTime": iso(start), "timeZone": tz},
        "end": {"dateTime": iso(start + duration), "timeZone": tz},
        "transparency": transparency,
        "organizer": {"email": "organizer@example.invalid"},
        "creator": {"email": "organizer@example.invalid"},
        "updated": iso(NOW),
    }
    if attendees:
        out["attendees"] = list(attendees)
    if recurrence:
        out["recurrence"] = list(recurrence)
    if recurring_event_id:
        out["recurringEventId"] = recurring_event_id
    if original_start is not None:
        out["originalStartTime"] = {"dateTime": iso(original_start), "timeZone": tz}
    return out


def raw_all_day(event_id: str, *, day: str | None = None, days: int = 1,
                summary: str = "Birthday", etag: str = '"v1"',
                status: str = "confirmed", recurrence=None) -> dict:
    day = day or day_of(SOON)
    end = (datetime.fromisoformat(day) + timedelta(days=days)).strftime("%Y-%m-%d")
    out: dict = {
        "id": event_id,
        "etag": etag,
        "status": status,
        "summary": summary,
        "iCalUID": f"{event_id}@google.com",
        "start": {"date": day},          # Google's end.date is EXCLUSIVE
        "end": {"date": end},
        "organizer": {"email": "organizer@example.invalid"},
        "updated": iso(NOW),
    }
    if recurrence:
        out["recurrence"] = list(recurrence)
    return out


def raw_deletion(event_id: str) -> dict:
    """What events.list sends for an event that no longer exists at all."""
    return {"id": event_id, "status": "cancelled", "etag": '"gone"'}


def raw_cancelled_occurrence(instance_id: str, *, master: str, original: int,
                             tz: str = "UTC") -> dict:
    """A cancelled *occurrence*: no start/end, only originalStartTime.

    Distinguishable from a deletion by the presence of recurringEventId, and
    the reason "this occurrence was cancelled" survives as a row rather than
    becoming "this occurrence never existed".
    """
    return {"id": instance_id, "status": "cancelled", "etag": '"cx"',
            "recurringEventId": master,
            "originalStartTime": {"dateTime": iso(original), "timeZone": tz}}


def raw_instance(instance_id: str, *, start: int, duration: int = 3600,
                 summary: str = "Standup", tz: str = "UTC", status: str = "confirmed",
                 location: str = "", attendees=()) -> dict:
    """One item of an events.instances response."""
    out = {"id": instance_id, "status": status, "summary": summary, "location": location,
           "start": {"dateTime": iso(start), "timeZone": tz},
           "end": {"dateTime": iso(start + duration), "timeZone": tz}}
    if attendees:
        out["attendees"] = list(attendees)
    return out


def self_attendee(response: str = "accepted") -> dict:
    return {"email": "me@example.invalid", "responseStatus": response, "self": True}
