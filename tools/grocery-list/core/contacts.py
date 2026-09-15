"""The sharing allowlist, and rendering a share without sending it."""

from __future__ import annotations

import sqlite3
from typing import Any

from errors import GroceryError
from items import current_items
from render import render_list
from text import clean_text, display_name, normalized, now_iso


def add_contact(conn: sqlite3.Connection, alias: str, channel: str, target: str) -> dict[str, str]:
    alias_clean = display_name(alias)
    channel_clean = clean_text(channel).casefold()
    target_clean = clean_text(target)
    if not channel_clean or not target_clean:
        raise GroceryError("contact channel and target are required")
    conn.execute(
        """
        INSERT INTO contacts(alias, normalized_alias, channel, target, added_at, active)
        VALUES (?, ?, ?, ?, ?, 1)
        ON CONFLICT(normalized_alias) DO UPDATE SET
            alias = excluded.alias,
            channel = excluded.channel,
            target = excluded.target,
            added_at = excluded.added_at,
            active = 1
        """,
        (alias_clean, normalized(alias), channel_clean, target_clean, now_iso()),
    )
    conn.commit()
    return {"alias": alias_clean, "channel": channel_clean, "target": target_clean}


def remove_contact(conn: sqlite3.Connection, alias: str) -> str:
    row = conn.execute(
        "SELECT * FROM contacts WHERE normalized_alias = ?", (normalized(alias),)
    ).fetchone()
    if not row or not row["active"]:
        raise GroceryError(f"contact is not allowlisted: {alias}")
    conn.execute("UPDATE contacts SET active = 0 WHERE id = ?", (row["id"],))
    conn.commit()
    return row["alias"]


def prepare_share(
    conn: sqlite3.Connection,
    store: str,
    contact: str,
    group_id: int | None = None,
) -> dict[str, Any]:
    """Render one household's list for an allowlisted contact.

    `group_id` is the caller's household. Without it the store lookup falls
    back to whichever group is first in the table, which meant a member of the
    second household could render the first household's list and send it to an
    external number.
    """
    person = conn.execute(
        "SELECT * FROM contacts WHERE normalized_alias = ? AND active = 1",
        (normalized(contact),),
    ).fetchone()
    if not person:
        raise GroceryError(f"contact is not allowlisted: {contact}")
    store, rows = current_items(conn, store, include_purchased=False, group_id=group_id)
    rendered_at = now_iso()
    payload = render_list(store, rows, rendered_at)
    cursor = conn.execute(
        "INSERT INTO shares(store_id, contact_id, rendered_at) VALUES (?, ?, ?)",
        (store["id"], person["id"], rendered_at),
    )
    conn.commit()
    return {
        "share_id": cursor.lastrowid,
        "contact": person["alias"],
        "channel": person["channel"],
        "target": person["target"],
        "payload": payload,
        "delivered": False,
    }


def mark_delivered(
    conn: sqlite3.Connection, share_id: int, group_id: int | None = None
) -> dict[str, Any]:
    clause, params = "", [share_id]
    if group_id is not None:
        # A share id is a small integer, so it is guessable. Confirming another
        # household's delivery is harmless in itself, but it confirms that
        # their share exists.
        clause = "AND store_id IN (SELECT id FROM stores WHERE group_id = ?)"
        params.append(group_id)
    row = conn.execute(
        f"SELECT * FROM shares WHERE id = ? {clause}", params
    ).fetchone()
    if not row:
        raise GroceryError(f"unknown share id: {share_id}")
    delivered = now_iso()
    conn.execute("UPDATE shares SET delivered_at = ? WHERE id = ?", (delivered, share_id))
    conn.commit()
    return {"share_id": share_id, "delivered_at": delivered}
