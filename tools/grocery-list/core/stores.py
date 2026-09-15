"""Store lookup, creation, and the never-ask store fallback."""

from __future__ import annotations

import os
import sqlite3

from errors import GroceryError
from people import default_store_for
from text import display_name, normalized, now_iso


DEFAULT_STORE = os.environ.get("GROCERY_DEFAULT_STORE", "").strip()


def resolve_store(
    conn: sqlite3.Connection,
    requested: str | None,
    group_id: int | None = None,
    actor: str | None = None,
) -> tuple[str, dict | None]:
    """Pick the store to act on, returning (name, assumption).

    Asking "which store?" costs a round trip that may not survive bad service,
    so fall back through progressively weaker but still reasonable guesses and
    report which one was used. `assumption` is None when the caller was explicit,
    and otherwise a structured record the presentation layer renders — the
    engine must not emit prose, or a Portuguese reply ends up half English.
    """
    if requested:
        return requested, None
    group_id = group_id if group_id is not None else default_group_id(conn)
    # A store the person named themselves outranks the household's last
    # activity: one is a stated preference, the other is an inference.
    chosen = default_store_for(conn, actor)
    if chosen:
        return chosen, {"kind": "store_personal_default", "store": chosen}
    # Only stores this group can see: another household's Costco is not a
    # candidate, and its activity must not steer this group's fallback.
    row = conn.execute(
        """
        SELECT e.store FROM events e
        WHERE e.group_id = ? AND EXISTS (
            SELECT 1 FROM stores s
            WHERE s.group_id = ? AND s.name = e.store
        )
        ORDER BY e.occurred_at DESC, e.id DESC LIMIT 1
        """,
        (group_id, group_id),
    ).fetchone()
    if row:
        return row["store"], {"kind": "store_last_touched", "store": row["store"]}
    stores = conn.execute(
        "SELECT name FROM stores WHERE group_id = ? LIMIT 2", (group_id,)
    ).fetchall()
    if len(stores) == 1:
        return stores[0]["name"], {"kind": "store_only", "store": stores[0]["name"]}
    if DEFAULT_STORE:
        return DEFAULT_STORE, {"kind": "store_default", "store": DEFAULT_STORE}
    raise GroceryError(
        "no store given, and no previous store, single store, or "
        "GROCERY_DEFAULT_STORE to fall back on"
    )


def default_group_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM groups ORDER BY id LIMIT 1").fetchone()
    if row is None:
        raise GroceryError("no group exists; the database was not initialized")
    return row["id"]


def store_row(
    conn: sqlite3.Connection,
    name: str,
    create: bool = False,
    group_id: int | None = None,
) -> sqlite3.Row:
    key = normalized(name)
    if not key:
        raise GroceryError("store name cannot be empty")
    group_id = group_id if group_id is not None else default_group_id(conn)
    row = conn.execute(
        "SELECT * FROM stores WHERE normalized_name = ? AND group_id = ?",
        (key, group_id),
    ).fetchone()
    if row:
        return row
    if not create:
        raise GroceryError(f"unknown store: {name}")
    clean = display_name(name)
    conn.execute(
        "INSERT INTO stores(group_id, name, normalized_name, created_at) VALUES (?, ?, ?, ?)",
        (group_id, clean, key, now_iso()),
    )
    return conn.execute(
        "SELECT * FROM stores WHERE normalized_name = ? AND group_id = ?",
        (key, group_id),
    ).fetchone()
