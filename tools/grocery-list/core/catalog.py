"""The household's learned product-to-section vocabulary."""

from __future__ import annotations

import sqlite3

import sections
from errors import GroceryError
from text import normalized, now_iso


def remember_section(
    conn: sqlite3.Connection, name: str, section: str, source: str = "user"
) -> str:
    if section not in sections.SECTION_KEYS:
        raise GroceryError(
            f"unknown section: {section} "
            f"(expected one of: {', '.join(sections.SECTION_KEYS)})"
        )
    conn.execute(
        """
        INSERT INTO product_sections(normalized_name, section, source, learned_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(normalized_name) DO UPDATE SET
            section = excluded.section,
            source = excluded.source,
            learned_at = excluded.learned_at
        """,
        (normalized(name), section, source, now_iso()),
    )
    return section


def section_for(conn: sqlite3.Connection, name: str) -> str | None:
    """Section for a product: what we learned, else the curated map, else None.

    None means genuinely unknown, and the caller is expected to classify it once
    and persist that with remember_section. Guessing here would bake a wrong
    answer into the household's vocabulary permanently.
    """
    row = conn.execute(
        "SELECT section FROM product_sections WHERE normalized_name = ?",
        (normalized(name),),
    ).fetchone()
    if row:
        return row["section"]
    guess = sections.classify(name)
    if guess:
        return remember_section(conn, name, guess, "curated")
    return None
