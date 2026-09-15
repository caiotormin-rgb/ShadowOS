"""Name normalization, quantities, and timezone-aware timestamps."""

from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

from errors import GroceryError


DEFAULT_TZ = os.environ.get("GROCERY_TIMEZONE", "America/New_York")


def now_iso() -> str:
    return datetime.now(ZoneInfo(DEFAULT_TZ)).replace(microsecond=0).isoformat()


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(DEFAULT_TZ))
    return parsed


# Python reads "+0500" and "+05:00:30" too, but SQLite's unixepoch() returns
# NULL for both — and a stamp it cannot read is never past retention.
_OFFSET = re.compile(r"(?:[+-]\d{2}:\d{2}|Z)$")


def validate_timestamp(value: str | None) -> str:
    if not value:
        return now_iso()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise GroceryError(f"invalid ISO timestamp: {value}") from exc
    if parsed.year < 1900:
        raise GroceryError(f"invalid ISO timestamp: {value} (year before 1900)")
    if parsed.tzinfo is not None and (
            parsed.utcoffset().total_seconds() % 60 or not _OFFSET.search(value.strip())):
        raise GroceryError(f"invalid ISO timestamp: {value} (offset must be ±HH:MM)")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(DEFAULT_TZ))
    return parsed.replace(microsecond=0).isoformat()


def clean_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"^[\s\-–—*•]+", "", value)
    value = re.sub(r"\s+", " ", value).strip(" ,.;")
    return value


def normalized(value: str) -> str:
    return clean_text(value).casefold()


def unit_filter(value: str | None) -> str | None:
    """Normalize a --unit argument the same way ingestion normalizes units.

    None means "any unit"; an empty string is a real filter that selects the
    unitless row.
    """
    return None if value is None else clean_text(value).casefold()


def display_name(value: str) -> str:
    value = clean_text(value)
    if not value:
        raise GroceryError("item name cannot be empty")
    return value[0].upper() + value[1:]


def quantity_text(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:g}"
