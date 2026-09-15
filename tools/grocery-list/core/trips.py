"""Closing a shopping trip, retry-safely, and undoing one."""

from __future__ import annotations

import os
import sqlite3
from typing import Any

import synonyms
from db import begin_write, record_event
from errors import GroceryError
from items import current_items
from stores import store_row
from text import normalized, now_iso, parse_timestamp, validate_timestamp


CLOSE_WINDOW_SECONDS = int(os.environ.get("GROCERY_CLOSE_WINDOW_SECONDS", "120"))


def close_trip(
    conn: sqlite3.Connection,
    store: str,
    closed_at: str | None = None,
    actor: str | None = None,
    group_id: int | None = None,
) -> dict[str, Any]:
    closed = validate_timestamp(closed_at)
    # The duplicate guard is itself check-then-insert: two retried closes at
    # once must not both conclude there is no recent trip.
    began = begin_write(conn)
    store = store_row(conn, store, group_id=group_id)

    # A duplicated or retried "close the trip" would otherwise open a second
    # trip and roll everything forward again, inventing a shopping trip that
    # never happened. Treat a close that follows another one closely, with no
    # item activity in between, as the same close.
    previous = conn.execute(
        "SELECT * FROM trips WHERE store_id = ? ORDER BY closed_at DESC, id DESC LIMIT 1",
        (store["id"],),
    ).fetchone()
    if previous is not None:
        elapsed = (parse_timestamp(closed) - parse_timestamp(previous["closed_at"])).total_seconds()
        if 0 <= elapsed <= CLOSE_WINDOW_SECONDS:
            # Compare against the close's own event ids, not its timestamp:
            # now_iso() has one-second resolution, so activity landing in the
            # same second as the close would be invisible to a > comparison.
            touched = conn.execute(
                """
                SELECT COUNT(*) FROM events
                WHERE store = ? AND group_id = ?
                  AND action IN ('added','merged','purchased','unpurchased','removed')
                  AND id > (SELECT COALESCE(MAX(id), 0) FROM events WHERE trip_id = ?)
                """,
                (store["name"], store["group_id"], previous["id"]),
            ).fetchone()[0]
            if not touched:
                archived = conn.execute(
                    "SELECT name, outcome FROM trip_items WHERE trip_id = ?",
                    (previous["id"],),
                ).fetchall()
                if began:
                    conn.commit()      # nothing written; release the write lock
                return {
                    "trip_id": previous["id"],
                    "store": store["name"],
                    "closed_at": previous["closed_at"],
                    "purchased": [r["name"] for r in archived if r["outcome"] == "purchased"],
                    "carried_forward": [r["name"] for r in archived if r["outcome"] != "purchased"],
                    "duplicate": True,
                }

    store, rows = current_items(conn, store["name"], True, group_id)
    if not rows:
        raise GroceryError(f"cannot close an empty list: {store['name']}")
    cursor = conn.execute(
        "INSERT INTO trips(store_id, closed_at, closed_by) VALUES (?, ?, ?)",
        (store["id"], closed, actor),
    )
    trip_id = cursor.lastrowid
    purchased: list[str] = []
    missing: list[str] = []
    for row in rows:
        outcome = "purchased" if row["status"] == "purchased" else "missing"
        conn.execute(
            """
            INSERT INTO trip_items(
                trip_id, name, quantity, unit, note, product_url, outcome, first_added_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trip_id, row["name"], row["quantity"], row["unit"], row["note"],
                row["product_url"],
                outcome, row["first_added_at"],
            ),
        )
        record_event(
            conn, occurred_at=closed, store=store["name"], name=row["name"],
            action="trip_purchased" if outcome == "purchased" else "trip_missing",
            quantity=float(row["quantity"]), unit=row["unit"], note=row["note"],
            product_url=row["product_url"],
            actor=actor, trip_id=trip_id, group_id=store["group_id"],
        )
        (purchased if outcome == "purchased" else missing).append(row["name"])
    conn.execute("DELETE FROM items WHERE store_id = ? AND status = 'purchased'", (store["id"],))
    conn.execute(
        "UPDATE items SET status = 'needed', updated_at = ? WHERE store_id = ?",
        (closed, store["id"]),
    )
    conn.commit()
    return {
        "trip_id": trip_id,
        "store": store["name"],
        "closed_at": closed,
        "purchased": purchased,
        "carried_forward": missing,
        "duplicate": False,
    }


def reopen_trip(
    conn: sqlite3.Connection,
    store: str,
    trip_id: int | None = None,
    actor: str | None = None,
    group_id: int | None = None,
) -> dict[str, Any]:
    """Undo a close, putting the archived trip back on the live list."""
    begin_write(conn)      # restores rows it first looks for: see begin_write
    store = store_row(conn, store, group_id=group_id)
    if trip_id is None:
        trip = conn.execute(
            "SELECT * FROM trips WHERE store_id = ? ORDER BY closed_at DESC, id DESC LIMIT 1",
            (store["id"],),
        ).fetchone()
        if trip is None:
            raise GroceryError(f"no closed trip to reopen for {store['name']}")
    else:
        trip = conn.execute(
            "SELECT * FROM trips WHERE id = ? AND store_id = ?", (trip_id, store["id"])
        ).fetchone()
        if trip is None:
            raise GroceryError(f"unknown trip for {store['name']}: {trip_id}")

    stamp = now_iso()
    restored: list[str] = []
    for row in conn.execute(
        "SELECT * FROM trip_items WHERE trip_id = ?", (trip["id"],)
    ).fetchall():
        status = "purchased" if row["outcome"] == "purchased" else "needed"
        canonical = synonyms.canonical(row["name"])
        # Either spelling is the same row: the trip archived "Leite", and the
        # list may have gained "Milk" since.
        existing = conn.execute(
            "SELECT id, product_url FROM items WHERE store_id = ? AND unit = ? "
            "AND (normalized_name = ? OR canonical_name = ?) "
            "ORDER BY normalized_name = ? DESC LIMIT 1",
            (store["id"], row["unit"], normalized(row["name"]), canonical,
             normalized(row["name"])),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE items SET status = ?, product_url = ?, updated_at = ? WHERE id = ?",
                (status, row["product_url"] or existing["product_url"], stamp, existing["id"]),
            )
        else:
            # With its dedup key: a blank one stops the row merging with its
            # other-language name, and the upgrade that repairs blanks runs once.
            conn.execute(
                """
                INSERT INTO items(
                    store_id, name, normalized_name, canonical_name, quantity,
                    unit, note, product_url, status, first_added_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    store["id"], row["name"], normalized(row["name"]), canonical,
                    row["quantity"], row["unit"], row["note"], row["product_url"], status,
                    row["first_added_at"], stamp,
                ),
            )
        record_event(
            conn, occurred_at=stamp, store=store["name"], name=row["name"],
            action="reopened", quantity=float(row["quantity"]), unit=row["unit"],
            note=row["note"], actor=actor, trip_id=trip["id"],
            product_url=row["product_url"],
            group_id=store["group_id"],
        )
        restored.append(row["name"])

    # trip_items cascades with the trip.
    conn.execute("DELETE FROM trips WHERE id = ?", (trip["id"],))
    conn.commit()
    return {"trip_id": trip["id"], "store": store["name"], "restored": restored}
