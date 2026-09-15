"""Aggregates over the event log, and what they imply is missing.

Everything here is computed on read. A household generates a few thousand
events in a year, which SQLite answers instantly, and a stale cached aggregate
is worse than a slow correct one.
"""

from __future__ import annotations

import sqlite3

import sections
from catalog import section_for
from text import normalized, now_iso, parse_timestamp


# A purchase is confirmed when a trip closes. `purchased` alone can be undone
# by `unbuy` before the trip ends, so counting it would inflate every total.
CONFIRMED = "trip_purchased"


def purchase_history(
    conn: sqlite3.Connection,
    store: str | None = None,
    limit: int = 200,
    group_id: int | None = None,
) -> list[dict]:
    """Per product: how often it was bought, and when it last was.

    Scoped by household, not by store name: two households may each have a
    Costco, and matching on the name alone reported one of them the other's
    purchases.
    """
    clause, params = "", []
    if store:
        clause = "AND store = ?"
        params.append(store)
    if group_id is not None:
        clause += " AND group_id = ?"
        params.append(group_id)
    rows = conn.execute(
        f"""
        SELECT normalized_name,
               MAX(item_name)   AS name,
               COUNT(*)         AS times,
               MIN(occurred_at) AS first_at,
               MAX(occurred_at) AS last_at
        FROM events
        WHERE action = '{CONFIRMED}' {clause}
        GROUP BY normalized_name
        ORDER BY times DESC, last_at DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def typical_interval_days(row: dict) -> float | None:
    """Mean days between purchases, or None when once is all we have seen."""
    if row["times"] < 2:
        return None
    span = (parse_timestamp(row["last_at"]) - parse_timestamp(row["first_at"])).total_seconds()
    gaps = row["times"] - 1
    return round(span / gaps / 86400, 1) if span > 0 else None


def days_since(row: dict, now: str | None = None) -> float:
    delta = parse_timestamp(now or now_iso()) - parse_timestamp(row["last_at"])
    return round(delta.total_seconds() / 86400, 1)


def on_list(
    conn: sqlite3.Connection, store: str, group_id: int | None = None
) -> set[str]:
    clause, params = "", [normalized(store)]
    if group_id is not None:
        clause = "AND s.group_id = ?"
        params.append(group_id)
    return {
        r["normalized_name"]
        for r in conn.execute(
            f"""
            SELECT i.normalized_name FROM items i
            JOIN stores s ON s.id = i.store_id
            WHERE s.normalized_name = ? {clause}
            """,
            params,
        )
    }


def due(
    conn: sqlite3.Connection,
    store: str,
    section: str | None = None,
    now: str | None = None,
    min_purchases: int = 2,
    slack: float = 1.0,
    group_id: int | None = None,
) -> list[dict]:
    """Products usually bought by now that are not on the list.

    `slack` multiplies the typical interval before something counts as due, so
    a weekly item is not nagged about on day six. Anything bought fewer than
    `min_purchases` times has no established rhythm and is left alone —
    suggesting from a single purchase is guessing, not remembering.
    """
    present = on_list(conn, store, group_id)
    out: list[dict] = []
    for row in purchase_history(conn, store, group_id=group_id):
        if row["normalized_name"] in present or row["times"] < min_purchases:
            continue
        interval = typical_interval_days(row)
        if interval is None:
            continue
        elapsed = days_since(row, now)
        if elapsed < interval * slack:
            continue
        key = section_for(conn, row["name"]) or sections.DEFAULT_SECTION
        if section and key != section:
            continue
        out.append({
            **row,
            "section": key,
            "days_since": elapsed,
            "typical_interval_days": interval,
            "overdue_by_days": round(elapsed - interval, 1),
        })
    out.sort(key=lambda r: r["overdue_by_days"], reverse=True)
    return out
