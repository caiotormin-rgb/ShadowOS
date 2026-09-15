"""The only place in the package allowed to import `logging`.

The plan's rule is: log ids, counts, durations, and error classes -- never event
titles, locations, attendees, or descriptions. A rule like that decays the
moment someone adds one convenient f-string, so it is enforced structurally:

* `log()` accepts only whitelisted field names and raises on anything else, so
  a stray `summary=` is a crash in the developer's face rather than a meeting
  title in the journal;
* `tests/test_logging.py` asserts no other module in `calctx` imports `logging`
  at all, so there is no second door.

A calendar id is deliberately NOT loggable. The primary calendar's id is the
user's email address, which makes it an identifier of a person rather than of a
row; pass `calendar_ref(cal_id)` instead, which is a stable short digest that is
useful for correlating log lines and useless for anything else.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

LOGGER_NAME = "calendar-context"

# Every field name a log line may carry. Adding one is a deliberate act.
ALLOWED_FIELDS = frozenset({
    "calendar_ref",     # digest, never the raw id -- see module docstring
    "event_id",
    "series_id",
    "instance_id",
    "run_id",
    "kind",
    "status",
    "error_class",
    "http_status",
    "count",
    "added",
    "updated",
    "deleted",
    "pruned",
    "instances",
    "pages",
    "series",
    "skipped",
    "elapsed_ms",
    "window_start_ts",
    "window_end_ts",
    "anchor_start_ts",
    "anchor_end_ts",
    "drift_seconds",
})


class ForbiddenLogField(ValueError):
    """A caller tried to log something that is calendar content, not metadata."""


def calendar_ref(calendar_id: str) -> str:
    """A stable, non-reversible handle for a calendar id.

    Correlates log lines across a run without writing an email address to disk.
    """
    return "cal:" + hashlib.sha256(calendar_id.encode("utf-8")).hexdigest()[:10]


def log(event: str, level: int = logging.INFO, **fields: Any) -> str:
    """Emit one structured line. Returns the rendered line so tests can read it.

    `event` is a fixed identifier chosen by the caller (a literal, never
    interpolated calendar text). Field values are rendered with repr-free str(),
    which is safe because every allowed field is an id, a count, or a class name.
    """
    bad = sorted(set(fields) - ALLOWED_FIELDS)
    if bad:
        raise ForbiddenLogField(
            f"not loggable: {', '.join(bad)}; calendar content must never reach a log"
        )
    rendered = " ".join(f"{k}={fields[k]}" for k in sorted(fields))
    line = f"{event} {rendered}".strip()
    logging.getLogger(LOGGER_NAME).log(level, line)
    return line
