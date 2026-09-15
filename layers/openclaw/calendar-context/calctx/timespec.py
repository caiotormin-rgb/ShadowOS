"""The all-day / timed distinction, and the rolling window.

This module exists because the single most plausible-looking way to corrupt a
calendar index is to turn an all-day event into an instant. A birthday is not
2026-08-23T00:00:00Z; it is a date, and it is the same date in Auckland and in
Sao Paulo. Once coerced, nothing downstream can tell it was ever a date.

So `TimeSpec` is a sum type with exactly two shapes, the schema enforces the
same split with CHECK constraints, and `order_ts` -- the fiction that lets an
agenda interleave dates with instants -- is quarantined here and documented as
a sort key that must never be returned as an instant.
"""
from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# The approved window: consulted forward, not archived backward.
#
# +12 months is the smallest horizon that materializes at least one instance of
# an annual series -- birthdays, renewals, anniversaries -- which is exactly the
# class of event a shorter window would silently hide.
# -3 months covers "when did I last see X", conflict checks against events that
# just passed, and matching a mail-context appointment candidate extracted weeks
# ago. Mail-context's 12-months-backward window is deliberately not mirrored:
# mail is an archive, a calendar is a schedule.
WINDOW_BACK_MONTHS = 3
WINDOW_FORWARD_MONTHS = 12

# How far the desired window may drift from a syncToken's anchor before the
# calendar is force-resynced. One month is both the drift limit and a cheap,
# self-healing correction for any accumulated incremental error.
ANCHOR_DRIFT_LIMIT_SECONDS = 31 * 86400

UTC = timezone.utc


class TimeSpecError(ValueError):
    """Google sent a start/end we refuse to guess about."""


@dataclass(frozen=True)
class TimeSpec:
    """Either an instant with its original zone, or a calendar date. Never both."""

    kind: str                 # 'timed' | 'all_day'
    utc: int | None = None    # epoch seconds; timed only
    tz: str | None = None     # IANA name, verbatim from Google; timed only
    date: str | None = None   # YYYY-MM-DD; all_day only

    def __post_init__(self) -> None:
        if self.kind == "timed":
            if self.utc is None or not self.tz or self.date is not None:
                raise TimeSpecError("a timed spec needs utc+tz and no date")
        elif self.kind == "all_day":
            if not self.date or self.utc is not None or self.tz is not None:
                raise TimeSpecError("an all-day spec needs a date and no instant")
        else:
            raise TimeSpecError(f"unknown start kind: {self.kind!r}")

    @property
    def all_day(self) -> bool:
        return self.kind == "all_day"


def parse_time(raw: dict | None, *, calendar_tz: str) -> TimeSpec | None:
    """Google's {dateTime, timeZone} or {date} -> TimeSpec.

    `timeZone` is often absent on the event itself, in which case Google means
    the owning calendar's zone; that is why calendarList.list (and its scope) is
    required rather than optional. Returns None for a payload with neither,
    which is what a cancelled instance looks like -- the caller decides how to
    recover, because only it knows the series.
    """
    if not raw:
        return None
    if raw.get("date"):
        return TimeSpec("all_day", date=str(raw["date"]))
    dt_raw = raw.get("dateTime")
    if not dt_raw:
        return None
    try:
        dt = datetime.fromisoformat(str(dt_raw).replace("Z", "+00:00"))
    except ValueError:
        raise TimeSpecError("unparseable dateTime") from None
    if dt.tzinfo is None:
        # Calendar always sends an offset; a naive value means a malformed
        # payload, and guessing a zone here is exactly the coercion this module
        # exists to prevent.
        raise TimeSpecError("dateTime without an offset")
    return TimeSpec("timed", utc=int(dt.timestamp()),
                    tz=str(raw.get("timeZone") or calendar_tz or "UTC"))


def zone(name: str | None) -> ZoneInfo | timezone:
    """A zone that never raises. An unknown IANA name degrades to UTC rather
    than taking down a sync run over one malformed calendar."""
    if not name:
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return UTC


def order_ts(spec: TimeSpec, *, calendar_tz: str) -> int:
    """The ordering aid, and nothing more.

    For a timed spec this is the real instant. For an all-day spec it is local
    midnight in the owning calendar's zone -- a value invented so an agenda can
    interleave the two shapes in one ORDER BY. It is never returned to a caller
    as an instant; `calctx.query` strips it from every row and returns
    `start_kind` alongside, so a caller cannot mistake one for the other.
    """
    if spec.kind == "timed":
        return int(spec.utc or 0)
    d = date.fromisoformat(str(spec.date))
    return int(datetime(d.year, d.month, d.day, tzinfo=zone(calendar_tz)).timestamp())


def add_months(dt: datetime, months: int) -> datetime:
    """Calendar-correct month arithmetic, clamping the day (Jan 31 + 1 = Feb 28).

    `now + 365 days` is not "+12 months" across a leap year, and the drift shows
    up as an annual series whose one materialized instance falls just outside
    the window.
    """
    total = dt.month - 1 + months
    year = dt.year + total // 12
    month = total % 12 + 1
    day = min(dt.day, monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def window_bounds(now: int | None = None, *, back_months: int = WINDOW_BACK_MONTHS,
                  forward_months: int = WINDOW_FORWARD_MONTHS) -> tuple[int, int]:
    """The -3/+12-month window as epoch seconds, recomputed on every run."""
    now = int(datetime.now(UTC).timestamp()) if now is None else int(now)
    base = datetime.fromtimestamp(now, UTC)
    return (int(add_months(base, -back_months).timestamp()),
            int(add_months(base, forward_months).timestamp()))


def rfc3339(ts: int) -> str:
    """timeMin/timeMax wire format. Always UTC, so no zone ambiguity travels."""
    return datetime.fromtimestamp(int(ts), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def date_range(start_date: str, end_date: str) -> tuple[str, str]:
    """Google's all-day `end.date` is exclusive; ours is inclusive.

    Storing the exclusive value would make a one-day event report as spanning
    two days in every 'is this date busy' check. Converted once, here.
    """
    end = date.fromisoformat(end_date) - timedelta(days=1)
    start = date.fromisoformat(start_date)
    return start_date, (end_date if end < start else end.isoformat())


def day_bounds(day: str, *, calendar_tz: str) -> tuple[int, int]:
    """[start, end) instants of a local calendar day, for all-day matching."""
    d = date.fromisoformat(day)
    zi = zone(calendar_tz)
    start = datetime(d.year, d.month, d.day, tzinfo=zi)
    return int(start.timestamp()), int((start + timedelta(days=1)).timestamp())
