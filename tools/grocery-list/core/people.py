"""Who uses the list, and the language they want back."""

from __future__ import annotations

import os
import re
import sqlite3
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from errors import GroceryError
from text import DEFAULT_TZ, clean_text, now_iso


LANGS = ("en", "pt")
DEFAULT_LANG = os.environ.get("GROCERY_LANG", "en").strip() or "en"

# Country calling code -> the zone most of that country's members live in.
# Longest prefix wins, so a future "+351" is not shadowed by a shorter code.
# A guess, which is why an operator can override it per person.
PHONE_TIMEZONES = {
    "+55": "America/Sao_Paulo",
    "+1": "America/New_York",
    "+351": "Europe/Lisbon",
    "+44": "Europe/London",
}


def phone_digits(actor: str | None) -> str:
    """The digits of an actor that looks like a phone number, else ''."""
    value = clean_text(actor or "")
    if not re.fullmatch(r"\+?[\d\s().-]+", value):
        return ""
    digits = re.sub(r"\D", "", value)
    return digits if 7 <= len(digits) <= 15 else ""


def timezone_from_phone(actor: str | None) -> str | None:
    digits = phone_digits(actor)
    if not digits:
        return None
    for prefix in sorted(PHONE_TIMEZONES, key=len, reverse=True):
        if f"+{digits}".startswith(prefix):
            return PHONE_TIMEZONES[prefix]
    return None


def timezone_for(conn: sqlite3.Connection, actor: str | None) -> str:
    """The zone to show this person times in.

    Order: an operator override stored on the person, then a guess from their
    number's country code, then the household default. An empty stored value
    means "not overridden", so a new member is right from their first message.
    """
    if actor:
        row = conn.execute(
            "SELECT timezone FROM people WHERE actor = ?", (clean_text(actor),)
        ).fetchone()
        # set_timezone validates, but a row edited by hand does not; an unknown
        # zone there would crash every read that renders a time.
        if row and row["timezone"] and _known_zone(row["timezone"]):
            return row["timezone"]
    return timezone_from_phone(actor) or DEFAULT_TZ


def _known_zone(zone: str) -> bool:
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def set_timezone(conn: sqlite3.Connection, actor: str, zone: str) -> dict[str, str]:
    """Operator override. `auto` clears it back to the country-code guess."""
    key = clean_text(actor)
    if not conn.execute("SELECT 1 FROM people WHERE actor = ?", (key,)).fetchone():
        raise GroceryError(f"{actor} is not on file; record them with `who set` first")
    stored = "" if zone.strip().casefold() == "auto" else zone.strip()
    if stored:
        try:
            ZoneInfo(stored)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise GroceryError(f"unknown timezone: {zone}") from exc
    conn.execute("UPDATE people SET timezone = ? WHERE actor = ?", (stored, key))
    conn.commit()
    return {"actor": key, "timezone": timezone_for(conn, key),
            "overridden": bool(stored)}


def public_label(conn: sqlite3.Connection, actor: str | None) -> str:
    """How one person is named to another: never their phone number.

    The name they go by, else the last four digits of the number, else the
    identifier itself when it is not a number at all. Empty for no actor.
    """
    if not actor:
        return ""
    row = conn.execute(
        "SELECT display_name FROM people WHERE actor = ?", (clean_text(actor),)
    ).fetchone()
    if row and row["display_name"]:
        return row["display_name"]
    digits = phone_digits(actor)
    if digits:
        return f"•••{digits[-4:]}"
    return clean_text(actor)


def remember_person(
    conn: sqlite3.Connection,
    actor: str,
    lang: str,
    display_name: str = "",
    default_store: str | None = None,
) -> dict[str, str]:
    key = clean_text(actor)
    if not key:
        raise GroceryError("actor cannot be empty")
    if lang not in LANGS:
        raise GroceryError(f"unknown language: {lang} (expected one of: {', '.join(LANGS)})")
    store = clean_text(default_store) if default_store is not None else None
    conn.execute(
        """
        INSERT INTO people(actor, display_name, lang, default_store, added_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(actor) DO UPDATE SET
            display_name = excluded.display_name,
            lang = excluded.lang,
            -- A roster sync must not wipe a store the person chose themselves.
            default_store = CASE WHEN ? IS NULL
                THEN people.default_store ELSE excluded.default_store END
        """,
        (key, clean_text(display_name), lang, store or "", now_iso(), store),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM people WHERE actor = ?", (key,)).fetchone()
    return {"actor": key, "lang": lang, "display_name": clean_text(display_name),
            "default_store": row["default_store"]}


def default_store_for(conn: sqlite3.Connection, actor: str | None) -> str | None:
    if not actor:
        return None
    row = conn.execute(
        "SELECT default_store FROM people WHERE actor = ?", (clean_text(actor),)
    ).fetchone()
    return row["default_store"] if row and row["default_store"] else None


def language_for(
    conn: sqlite3.Connection, actor: str | None, explicit: str | None = None
) -> str:
    """Resolve the reply language.

    Order: what this request explicitly asked for, then the requester's stored
    preference, then the configured default. Deliberately never the language of
    the items — when someone just pastes a list, the items are the whole
    message and reading the language off them is a coin flip, not a signal.
    """
    if explicit:
        return explicit
    if actor:
        row = conn.execute(
            "SELECT lang FROM people WHERE actor = ?", (clean_text(actor),)
        ).fetchone()
        if row:
            return row["lang"]
    return DEFAULT_LANG
