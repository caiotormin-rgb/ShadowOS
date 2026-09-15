"""Who changed what on the list, and when — read from the event log.

The member-facing view of `events`. Three properties are load-bearing, and
each is easy to lose without noticing:

- **One household.** Every query is keyed on the caller's group id, and so is
  resolving `--by`: a name that belongs to another household matches nobody.
- **No phone numbers.** People are shown by `people.public_label`.
- **The reader's clock.** Times are rendered, and relative dates like `today`
  are resolved, in the viewer's zone — not UTC, and not the server's.

Item, store and person names are text other people typed, and this output is
sent over WhatsApp and read by a model, so the rendered half is made inert
(`safe`). The JSON half keeps the stored values as they are.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import people
import synonyms
from errors import GroceryError
from stores import store_row
from text import DEFAULT_TZ, clean_text, parse_timestamp, quantity_text


DEFAULT_LIMIT = 20
MAX_LIMIT = 100

# Filter name -> the log actions it covers. A close writes one trip_* row per
# item; the feed shows it as one "closed" line.
ACTIONS: dict[str, tuple[str, ...]] = {
    "added": ("added",),
    "merged": ("merged",),
    "purchased": ("purchased",),
    "unpurchased": ("unpurchased",),
    "removed": ("removed",),
    "closed": ("trip_purchased", "trip_missing"),
    "reopened": ("reopened",),
}
KIND = {log: kind for kind, logs in ACTIONS.items() for log in logs}
GROUPED = {"closed", "reopened"}


def visible_actors(
    conn: sqlite3.Connection, viewer: str | None, group_id: int
) -> set[str] | None:
    """Whose activity `viewer` may see; None means the whole household.

    The list is shared and attributed — onboarding tells every member that
    everyone sees what they add and who added it — so today every member sees
    all of it. This is the one place to narrow that, should the household ever
    want to.
    """
    return None


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, int(limit)))


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

_RELATIVE = re.compile(r"(\d{1,4})([hdw])")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_SPAN = {"h": "hours", "d": "days", "w": "weeks"}
TODAY_WORDS = {"today", "hoje"}
YESTERDAY_WORDS = {"yesterday", "ontem"}


def parse_bound(value: str, zone: str, now: datetime, end: bool = False) -> datetime:
    """Turn `--since`/`--until` into an instant, in the viewer's zone.

    A day (`today`, `yesterday`, `2026-09-10`) starts at local midnight; as an
    upper bound it means the whole day, so it ends at the next midnight. A span
    (`12h`, `7d`, `2w`) counts back from now. A datetime without an offset is
    local. At 23:30 in Sao Paulo it is already tomorrow in UTC; `today` still
    means the Sao Paulo day.
    """
    tz = ZoneInfo(zone)
    text = (value or "").strip().casefold()
    local_today = now.astimezone(tz).date()
    relative = _RELATIVE.fullmatch(text)
    try:
        if text in TODAY_WORDS:
            day = local_today
        elif text in YESTERDAY_WORDS:
            day = local_today - timedelta(days=1)
        elif relative:
            span = timedelta(**{_SPAN[relative.group(2)]: int(relative.group(1))})
            # In UTC: arithmetic on an aware datetime in one zone is wall-clock
            # arithmetic, so 12h across a DST change covered 13 or 11 hours.
            return _in_range((now.astimezone(timezone.utc) - span).astimezone(tz), value)
        elif _DATE.fullmatch(text):
            day = date.fromisoformat(text)
        else:
            parsed = datetime.fromisoformat(value.strip())
            return _in_range(parsed if parsed.tzinfo else parsed.replace(tzinfo=tz), value)
        # Checked before adding a day: 9999-12-31 plus one overflows.
        _in_range(day, value)
        if end:
            day += timedelta(days=1)
        return datetime.combine(day, time(0), tzinfo=tz)
    except (ValueError, OverflowError, AttributeError) as exc:
        raise _bad_date(value) from exc


# A household list has no history before 1970 and no use for dates past 2100.
# Bounding them keeps every later step (offsets, the SQL date margin, the
# +1 day of an upper bound) far from datetime's year 1 / 9999 limits, where it
# raised OverflowError and the plugin forwarded the traceback to the agent.
MIN_YEAR, MAX_YEAR = 1970, 2100


def _in_range(moment, value: str):
    if not MIN_YEAR <= moment.year <= MAX_YEAR:
        raise _bad_date(value)
    return moment


def _bad_date(value: str) -> GroceryError:
    return GroceryError(
        f"unrecognized date: {value} (use today, yesterday, 12h, 7d, 2w, or "
        f"YYYY-MM-DD between {MIN_YEAR} and {MAX_YEAR})"
    )


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------

def resolve_by(conn: sqlite3.Connection, group_id: int, query: str) -> set[str]:
    """The actors in this household that `query` names. Never looks further.

    Candidates are the household's members plus anyone its own log attributes
    something to (a member who has since left still has history). A query
    matches an exact identifier, the last digits of a number, a display name,
    or its first word — case- and accent-insensitively.
    """
    candidates = {
        r["actor"] for r in conn.execute(
            "SELECT actor FROM group_members WHERE group_id = ?", (group_id,))
    } | {
        r["actor"] for r in conn.execute(
            "SELECT DISTINCT actor FROM events "
            "WHERE group_id = ? AND actor IS NOT NULL AND actor != ''", (group_id,))
    }
    wanted = synonyms.fold(query)
    looks_numeric = bool(re.fullmatch(r"[\d\s+().-]+", query.strip()))
    digits = re.sub(r"\D", "", query)
    matched: set[str] = set()
    for actor in candidates:
        if clean_text(actor) == clean_text(query):
            matched.add(actor)
            continue
        actor_digits = people.phone_digits(actor)
        if looks_numeric and len(digits) >= 4 and actor_digits.endswith(digits):
            matched.add(actor)
            continue
        row = conn.execute(
            "SELECT display_name FROM people WHERE actor = ?", (actor,)
        ).fetchone()
        name = synonyms.fold(row["display_name"]) if row and row["display_name"] else ""
        if wanted and name and (name == wanted or name.split()[0] == wanted):
            matched.add(actor)
    return matched


def item_matcher(query: str) -> Callable[[str], bool]:
    """Synonym-aware: `leite` finds Milk, and a qualified Leite desnatado.

    A name that merely contains the word is a different product — Doce de
    leite is not milk — so beyond the synonym match, only names that *start*
    with the query count.
    """
    target = synonyms.canonical(query)
    folded = synonyms.fold(query)

    def matches(name: str) -> bool:
        if target and synonyms.canonical(name) == target:
            return True
        return bool(folded) and f"{synonyms.fold(name)} ".startswith(f"{folded} ")

    return matches


# --------------------------------------------------------------------------
# The feed
# --------------------------------------------------------------------------

def report(
    conn: sqlite3.Connection,
    viewer: str | None,
    group_id: int | None,
    lang: str = "en",
    *,
    since: str | None = None,
    until: str | None = None,
    by: str | None = None,
    item: str | None = None,
    store: str | None = None,
    action: str | None = None,
    limit: int | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Household activity as JSON-ready data, with its rendered text under `text`."""
    if group_id is None:
        raise GroceryError("activity needs the caller's household")
    if action is not None and action not in ACTIONS:
        raise GroceryError(f"unknown action: {action} (expected one of: {', '.join(ACTIONS)})")
    zone = people.timezone_for(conn, viewer)
    tz = ZoneInfo(zone)
    current = parse_timestamp(now) if now else datetime.now(tz)
    start = parse_bound(since, zone, current) if since else None
    stop = parse_bound(until, zone, current, end=True) if until else None
    limit = clamp_limit(limit)

    clauses, params = ["group_id = ?"], [group_id]
    visible = visible_actors(conn, viewer, group_id)
    if visible is not None:
        clauses.append(f"actor IN ({', '.join('?' * len(visible))})")
        params.extend(sorted(visible))
    store_name = None
    if store:
        store_name = store_row(conn, store, group_id=group_id)["name"]
        clauses.append("store = ?")
        params.append(store_name)
    if action:
        clauses.append(f"action IN ({', '.join('?' * len(ACTIONS[action]))})")
        params.extend(ACTIONS[action])
    by_matched = None
    if by is not None:
        actors = resolve_by(conn, group_id, by)
        by_matched = bool(actors)
        clauses.append(f"actor IN ({', '.join('?' * len(actors))})" if actors else "0")
        params.extend(sorted(actors))
    # Timestamps carry their own offsets, so string order is not time order
    # across a DST change. SQL narrows by date with a margin; Python decides.
    if start:
        clauses.append("occurred_at >= ?")
        params.append((start - timedelta(days=2)).date().isoformat())
    if stop:
        clauses.append("occurred_at < ?")
        params.append((stop + timedelta(days=2)).date().isoformat())
    rows = conn.execute(
        f"SELECT * FROM events WHERE {' AND '.join(clauses)}", params
    ).fetchall()

    matches = item_matcher(item) if item else None
    picked = []
    for row in rows:
        at = parse_timestamp(row["occurred_at"])
        if (start and at < start) or (stop and at >= stop):
            continue
        if matches and not matches(row["item_name"]):
            continue
        picked.append((at, row))
    picked.sort(key=lambda pair: (pair[0], pair[1]["id"]), reverse=True)

    entries = _entries(conn, picked, tz)
    truncated = len(entries) > limit
    entries = entries[:limit]
    result = {
        "timezone": zone,
        "household_timezone": DEFAULT_TZ,
        "since": start.astimezone(tz).isoformat() if start else None,
        "until": stop.astimezone(tz).isoformat() if stop else None,
        "by": by,
        "by_matched": by_matched,
        "item": item,
        "store": store_name,
        "action": action,
        "limit": limit,
        "count": len(entries),
        "truncated": truncated,
        "entries": entries,
    }
    result["text"] = render(result, lang, current.astimezone(tz).date())
    return result


def _entries(conn, picked, tz) -> list[dict[str, Any]]:
    labels: dict[str | None, str] = {}
    entries: list[dict[str, Any]] = []
    groups: dict[tuple, dict[str, Any]] = {}
    for at, row in picked:
        kind = KIND[row["action"]]
        actor = row["actor"]
        if actor not in labels:
            labels[actor] = people.public_label(conn, actor)
        local = at.astimezone(tz)
        base = {
            "at": row["occurred_at"],
            "date": local.date().isoformat(),
            "time": local.strftime("%H:%M"),
            "actor": labels[actor],
            "action": kind,
            "store": row["store"],
            "source_type": row["source_type"],
        }
        if kind in GROUPED:
            key = (kind, row["trip_id"], row["occurred_at"], actor, row["store"])
            entry = groups.get(key)
            if entry is None:
                entry = {**base, "item": None}
                if kind == "closed":
                    entry.update(bought=[], missing=[])
                else:
                    entry["items"] = []
                groups[key] = entry
                entries.append(entry)
            bucket = {"trip_purchased": "bought", "trip_missing": "missing"}.get(
                row["action"], "items")
            entry[bucket].append(row["item_name"])
            continue
        entries.append({
            **base,
            "item": row["item_name"],
            "quantity": row["quantity"],
            "unit": row["unit"],
            "previous_quantity": row["previous_quantity"],
        })
    for entry in groups.values():
        for bucket in ("bought", "missing", "items"):
            if bucket in entry:
                entry[bucket].sort(key=synonyms.fold)
    return entries


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

HEADER = {"en": "List activity", "pt": "Atividade da lista"}
ZONE_LABEL = {"en": " ({city} time)", "pt": " (horário de {city})"}
CITIES = {
    "America/Sao_Paulo": {"en": "São Paulo", "pt": "São Paulo"},
    "America/New_York": {"en": "New York", "pt": "Nova York"},
}
EMPTY = {"en": "No activity found.", "pt": "Nenhuma atividade encontrada."}
NO_ONE = {"en": "No one in the household matches {by}.",
          "pt": "Ninguém da casa corresponde a {by}."}
# Counts changes, not lines: a batch of seven purchases is one line.
TRUNCATED = {"en": "Showing the {n} most recent changes; ask for more or narrow the dates.",
             "pt": "Mostrando as {n} mudanças mais recentes; peça mais ou restrinja as datas."}
SOMEONE = {"en": "Someone", "pt": "Alguém"}
TODAY = {"en": "today", "pt": "hoje"}
YESTERDAY = {"en": "yesterday", "pt": "ontem"}
WEEKDAYS = {"en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
            "pt": ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")}
MONTHS = {"en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
          "pt": ("jan", "fev", "mar", "abr", "mai", "jun",
                 "jul", "ago", "set", "out", "nov", "dez")}

VERBS = {
    "added":       {"en": "added {item}", "pt": "adicionou {item}"},
    "purchased":   {"en": "bought {item}", "pt": "comprou {item}"},
    "unpurchased": {"en": "put {item} back on the list", "pt": "devolveu {item} pra lista"},
    "removed":     {"en": "removed {item}", "pt": "removeu {item}"},
    "changed":     {"en": "updated {name}: {before} → {after}",
                    "pt": "atualizou {name}: {before} → {after}"},
    "unchanged":   {"en": "updated {name}: still {after}", "pt": "atualizou {name}: continua {after}"},
    "merged":      {"en": "updated {name} (now {after})", "pt": "atualizou {name} (agora {after})"},
    "closed":      {"en": "closed the trip", "pt": "fechou a compra"},
    "bought":      {"en": "bought: {names}", "pt": "comprados: {names}"},
    "missing":     {"en": "carried over: {names}", "pt": "pendentes: {names}"},
    "reopened":    {"en": "reopened the trip", "pt": "reabriu a compra"},
}

# WhatsApp formats *bold*, _italic_, ~strike~ and `code`. Lookalikes keep a
# name readable without letting it restyle, or seem to end, the message.
_INERT = str.maketrans({"*": "∗", "_": "ˍ", "~": "∼", "`": "ˋ"})


def safe(value: Any, limit: int = 80) -> str:
    """One inert line of someone else's text.

    Line and paragraph breaks become spaces, so a name cannot forge a line of
    its own; invisible format characters (bidi overrides, zero-width) go;
    formatting marks are swapped for lookalikes; very long names are cut.
    """
    out = []
    for ch in str(value):
        category = unicodedata.category(ch)
        if category in ("Cc", "Zl", "Zp"):
            out.append(" ")
        elif category != "Cf":
            out.append(ch)
    text = re.sub(r"\s+", " ", "".join(out)).strip().translate(_INERT)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _amount(quantity: float | None, unit: str) -> str:
    number = quantity_text(float(quantity if quantity is not None else 1))
    # Someone else typed the unit too; it gets the same treatment as a name.
    unit = safe(unit, 20) if unit else ""
    return f"{number} {unit}" if unit else number


def _item(entry: dict) -> str:
    name = safe(entry["item"])
    quantity = entry["quantity"]
    if (quantity is None or float(quantity) == 1) and not entry["unit"]:
        return name
    return f"{name} x{_amount(quantity, entry['unit'])}"


def _names(names: list[str], most: int = 5) -> str:
    shown = ", ".join(safe(n, 40) for n in names[:most])
    return f"{shown} +{len(names) - most}" if len(names) > most else shown


def _phrase(entry: dict, lang: str) -> str:
    kind = entry["action"]
    if kind == "merged":
        after = _amount(entry["quantity"], entry["unit"])
        name = safe(entry["item"])
        before = entry["previous_quantity"]
        if before is None:
            return VERBS["merged"][lang].format(name=name, after=after)
        if float(before) == float(entry["quantity"] or 0):
            return VERBS["unchanged"][lang].format(name=name, after=after)
        return VERBS["changed"][lang].format(
            name=name, before=_amount(before, entry["unit"]), after=after)
    if kind == "closed":
        parts = [VERBS[b][lang].format(names=_names(entry[b]))
                 for b in ("bought", "missing") if entry[b]]
        return f"{VERBS['closed'][lang]} — {'; '.join(parts)}"
    if kind == "reopened":
        return f"{VERBS['reopened'][lang]} — {_names(entry['items'])}"
    return VERBS[kind][lang].format(item=_item(entry))


def _clock(entry: dict, lang: str) -> str:
    hour, minute = (int(part) for part in entry["time"].split(":"))
    if lang == "pt":
        return f"{hour:02d}:{minute:02d}"
    return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def _day(day: date, today: date, lang: str) -> str:
    weekday, month = WEEKDAYS[lang][day.weekday()], MONTHS[lang][day.month - 1]
    label = f"{weekday} {day.day} {month}" if lang == "pt" else f"{weekday} {month} {day.day}"
    if day.year != today.year:
        label = f"{label} {day.year}"
    if day == today:
        return f"{TODAY[lang]}, {label}"
    if day == today - timedelta(days=1):
        return f"{YESTERDAY[lang]}, {label}"
    return label


# Changes one message makes to several items at once. A merge stays on its own
# line, because its before-and-after is the point of showing it.
BATCHABLE = {"added", "purchased", "unpurchased", "removed"}


def _batches(entries: list[dict]) -> list[list[dict]]:
    """Adjacent changes of one kind, by one person, at one store, in the same
    second — one message — become one line. The JSON keeps every change."""
    batches: list[list[dict]] = []
    for entry in entries:
        last = batches[-1][0] if batches else None
        if (last and entry["action"] in BATCHABLE
                and all(entry[k] == last[k] for k in ("action", "at", "actor", "store"))):
            batches[-1].append(entry)
        else:
            batches.append([entry])
    return batches


def _batch_items(batch: list[dict], most: int = 8) -> str:
    ordered = sorted(batch, key=lambda e: synonyms.fold(e["item"]))
    shown = ", ".join(_item(e) for e in ordered[:most])
    return f"{shown} +{len(ordered) - most}" if len(ordered) > most else shown


def render(result: dict, lang: str, today: date) -> str:
    """WhatsApp-safe text: no tables or headings, days in bold, newest first."""
    header = HEADER[lang]
    if result["timezone"] != result["household_timezone"]:
        zone = result["timezone"]
        city = CITIES.get(zone, {}).get(lang) or zone.split("/")[-1].replace("_", " ")
        header += ZONE_LABEL[lang].format(city=city)
    out = [header]
    if result["by_matched"] is False:
        return "\n".join(out + ["", NO_ONE[lang].format(by=safe(result["by"], 40))])
    if not result["entries"]:
        return "\n".join(out + ["", EMPTY[lang]])
    current_day = None
    for batch in _batches(result["entries"]):
        entry = batch[0]
        day = date.fromisoformat(entry["date"])
        if day != current_day:
            out += ["", f"*{_day(day, today, lang)}*"]
            current_day = day
        who = safe(entry["actor"], 40) or SOMEONE[lang]
        phrase = (_phrase(entry, lang) if len(batch) == 1
                  else VERBS[entry["action"]][lang].format(item=_batch_items(batch)))
        out.append(f"- {_clock(entry, lang)} · {who} {phrase} · {safe(entry['store'], 40)}")
    if result["truncated"]:
        out += ["", TRUNCATED[lang].format(n=result["count"])]
    return "\n".join(out)
