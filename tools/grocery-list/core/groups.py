"""Households: who may read and contribute to which lists."""

from __future__ import annotations

import sqlite3

from db import DEFAULT_GROUP
from errors import GroceryError
from text import clean_text, display_name, normalized, now_iso


ROLES = ("owner", "member")


def group_row(
    conn: sqlite3.Connection, name: str | None = None, create: bool = False
) -> sqlite3.Row:
    key = normalized(name or DEFAULT_GROUP)
    if not key:
        raise GroceryError("group name cannot be empty")
    row = conn.execute(
        "SELECT * FROM groups WHERE normalized_name = ?", (key,)
    ).fetchone()
    if row:
        return row
    if not create:
        raise GroceryError(f"unknown group: {name}")
    conn.execute(
        "INSERT INTO groups(name, normalized_name, created_at) VALUES (?, ?, ?)",
        (display_name(name or DEFAULT_GROUP), key, now_iso()),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM groups WHERE normalized_name = ?", (key,)
    ).fetchone()


def add_member(
    conn: sqlite3.Connection, group: str | None, actor: str, role: str = "member"
) -> dict:
    if role not in ROLES:
        raise GroceryError(f"unknown role: {role} (expected one of: {', '.join(ROLES)})")
    key = clean_text(actor)
    if not key:
        raise GroceryError("member identifier cannot be empty")
    row = group_row(conn, group, create=True)
    conn.execute(
        """
        INSERT INTO group_members(group_id, actor, role, added_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(group_id, actor) DO UPDATE SET role = excluded.role
        """,
        (row["id"], key, role, now_iso()),
    )
    conn.commit()
    return {"group": row["name"], "actor": key, "role": role}


def remove_member(conn: sqlite3.Connection, group: str | None, actor: str) -> str:
    row = group_row(conn, group)
    cursor = conn.execute(
        "DELETE FROM group_members WHERE group_id = ? AND actor = ?",
        (row["id"], clean_text(actor)),
    )
    conn.commit()
    if not cursor.rowcount:
        raise GroceryError(f"{actor} is not a member of {row['name']}")
    return clean_text(actor)


def members(conn: sqlite3.Connection, group: str | None = None) -> list[dict]:
    row = group_row(conn, group)
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT m.actor, m.role, m.added_at, COALESCE(p.display_name, '') AS name,
                   COALESCE(p.lang, '') AS lang
            FROM group_members m
            LEFT JOIN people p ON p.actor = m.actor
            WHERE m.group_id = ?
            ORDER BY m.role, m.actor
            """,
            (row["id"],),
        )
    ]


def group_for_actor(conn: sqlite3.Connection, actor: str | None) -> sqlite3.Row:
    """The group a person acts in, defaulting to the household.

    An unconfigured deployment has no members at all, and refusing everyone
    there would lock the owner out of their own list. Once anyone has been
    added, membership is required — the gate closes when it is configured, not
    before.
    """
    if actor:
        row = conn.execute(
            """
            SELECT g.* FROM groups g
            JOIN group_members m ON m.group_id = g.id
            WHERE m.actor = ?
            ORDER BY g.id LIMIT 1
            """,
            (clean_text(actor),),
        ).fetchone()
        if row:
            return row

    default = group_row(conn, None, create=True)
    enrolled = conn.execute(
        "SELECT COUNT(*) FROM group_members WHERE group_id = ?", (default["id"],)
    ).fetchone()[0]
    if enrolled and actor:
        raise GroceryError(
            f"{actor} is not a member of any list; ask an owner to add them"
        )
    if enrolled and not actor:
        raise GroceryError(
            "this list has members, so an --actor is required to identify the caller"
        )
    return default
