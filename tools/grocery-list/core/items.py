"""Item parsing, ingestion, lookup, status, and removal."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

import synonyms
from db import begin_write, record_event
from errors import GroceryError
from stores import store_row
from text import clean_text, display_name, normalized, now_iso, validate_timestamp


MAX_PRODUCT_URL_LENGTH = 2048


def normalize_product_url(value: Any) -> str:
    """Validate and normalize a URL supplied by OpenClaw after page scraping.

    This function deliberately performs no DNS lookup or HTTP request. It only
    accepts absolute HTTP(S) URLs without credentials, removes fragments, and
    normalizes the scheme/host/default port for stable storage.
    """
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise GroceryError("product URL must be a string")
    value = value.strip()
    if not value or len(value) > MAX_PRODUCT_URL_LENGTH:
        raise GroceryError("product URL must be 1 to 2048 characters")
    if any(ord(char) <= 32 or ord(char) == 127 for char in value):
        raise GroceryError("product URL must not contain whitespace or control characters")
    try:
        parts = urlsplit(value)
        port = parts.port  # forces validation of malformed/out-of-range ports
        host = parts.hostname
    except ValueError as exc:
        raise GroceryError("invalid product URL") from exc
    scheme = parts.scheme.casefold()
    if scheme not in {"http", "https"} or not host:
        raise GroceryError("product URL must be an absolute http or https URL")
    if parts.username is not None or parts.password is not None:
        raise GroceryError("product URL must not contain credentials")
    try:
        ascii_host = host.encode("idna").decode("ascii").casefold()
    except UnicodeError as exc:
        raise GroceryError("invalid product URL hostname") from exc
    if ":" in ascii_host:  # urlsplit strips IPv6 brackets
        ascii_host = f"[{ascii_host}]"
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = ascii_host if port is None or default_port else f"{ascii_host}:{port}"
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def parse_items_json(payload: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise GroceryError(f"invalid items JSON: {exc.msg}") from exc
    if not isinstance(data, list):
        raise GroceryError("items JSON must be a list")
    parsed: list[dict[str, Any]] = []
    for entry in data:
        if isinstance(entry, str):
            entry = {"name": entry}
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise GroceryError("each item must be a string or an object with a name")
        try:
            quantity = float(entry.get("quantity", 1))
        except (TypeError, ValueError) as exc:
            raise GroceryError(f"invalid quantity for {entry.get('name')}") from exc
        # nan compares false with everything, so it passed `<= 0` and failed
        # later as an IntegrityError; inf is not a quantity anyone buys.
        if not math.isfinite(quantity):
            raise GroceryError(f"invalid quantity for {entry.get('name')}")
        if quantity <= 0:
            raise GroceryError("quantity must be greater than zero")
        supplied_url = entry.get("productUrl", entry.get("product_url", ""))
        parsed.append(
            {
                "name": display_name(entry["name"]),
                "quantity": quantity,
                "unit": clean_text(str(entry.get("unit", ""))).casefold(),
                "note": clean_text(str(entry.get("note", ""))),
                "product_url": normalize_product_url(supplied_url),
            }
        )
    if not parsed:
        raise GroceryError("at least one item is required")
    return parsed


def ingest_items(
    conn: sqlite3.Connection,
    store: str,
    items: Iterable[dict[str, Any]],
    source_type: str,
    source_ref: str = "",
    raw_text: str = "",
    observed_at: str | None = None,
    actor: str | None = None,
    group_id: int | None = None,
) -> dict[str, Any]:
    observed = validate_timestamp(observed_at)
    begin_write(conn)      # before looking the store and items up: see begin_write
    store = store_row(conn, store, create=True, group_id=group_id)
    added: list[str] = []
    merged: list[str] = []
    for item in items:
        key = normalized(item["name"])
        canonical = synonyms.canonical(item["name"])
        unit = item.get("unit", "")
        product_url = item.get("product_url", "")
        # Matched on the canonical key, so one household writing "leite" and
        # another writing "milk" lands on one row instead of two.
        existing = conn.execute(
            "SELECT * FROM items WHERE store_id = ? AND canonical_name = ? AND unit = ?",
            (store["id"], canonical, unit),
        ).fetchone()
        if existing:
            quantity = max(float(existing["quantity"]), float(item.get("quantity", 1)))
            note = item.get("note", "") or existing["note"]
            product_url = product_url or existing["product_url"]
            # Keep the name already on the list: whoever wrote it first gets
            # to see their own words, and the canonical key does the matching.
            conn.execute(
                """
                UPDATE items
                SET quantity = ?, note = ?, product_url = ?, status = 'needed', updated_at = ?
                WHERE id = ?
                """,
                (quantity, note, product_url, observed, existing["id"]),
            )
            item_id = existing["id"]
            merged.append(item["name"])
            record_event(
                conn, occurred_at=observed, store=store["name"], name=item["name"],
                action="merged", quantity=quantity, unit=unit, note=note,
                source_type=source_type, source_ref=source_ref, actor=actor,
                group_id=store["group_id"], raw_text=raw_text,
                previous_quantity=float(existing["quantity"]),
                product_url=product_url,
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO items(
                    store_id, name, normalized_name, canonical_name, quantity,
                    unit, note, product_url, status, first_added_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'needed', ?, ?)
                """,
                (
                    store["id"], item["name"], key, canonical,
                    float(item.get("quantity", 1)), unit, item.get("note", ""),
                    product_url,
                    observed, observed,
                ),
            )
            item_id = cursor.lastrowid
            added.append(item["name"])
            record_event(
                conn, occurred_at=observed, store=store["name"], name=item["name"],
                action="added", quantity=float(item.get("quantity", 1)), unit=unit,
                note=item.get("note", ""), source_type=source_type,
                source_ref=source_ref, actor=actor, group_id=store["group_id"],
                raw_text=raw_text,
                product_url=product_url,
            )
        conn.execute(
            """
            INSERT INTO item_sources(item_id, source_type, source_ref, raw_text, observed_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (item_id, source_type, source_ref, raw_text, observed),
        )
    conn.commit()
    return {"store": store["name"], "added": added, "merged": merged, "observed_at": observed}


def current_items(
    conn: sqlite3.Connection,
    store: str,
    include_purchased: bool = True,
    group_id: int | None = None,
) -> tuple[sqlite3.Row, list[sqlite3.Row]]:
    store = store_row(conn, store, group_id=group_id)
    clause = "" if include_purchased else "AND status = 'needed'"
    rows = conn.execute(
        f"""
        SELECT * FROM items
        WHERE store_id = ? {clause}
        ORDER BY CASE status WHEN 'needed' THEN 0 ELSE 1 END, normalized_name
        """,
        (store["id"],),
    ).fetchall()
    return store, rows


def resolve_item(
    conn: sqlite3.Connection,
    store: sqlite3.Row,
    name: str,
    unit: str | None = None,
    assume: bool = False,
) -> tuple[sqlite3.Row, dict | None]:
    """Resolve a name to one item, returning (row, assumption).

    Items are unique per (store, name, unit), so a bare name can match several
    rows differing only by unit. Which way to break the tie depends on how
    expensive a mistake is. With `assume` the caller is doing something
    reversible, so prefer the row still needed, then the most recently touched,
    and report the choice. Without it the caller is deleting something with no
    undo, so an ambiguous name stays an error the user settles explicitly.
    """
    params: list[Any] = [store["id"], normalized(name), synonyms.canonical(name)]
    clause = ""
    if unit is not None:
        clause = " AND unit = ?"
        params.append(unit)
    # Either spelling finds the row: "buy milk" reaches an item added as Leite.
    rows = conn.execute(
        f"SELECT * FROM items WHERE store_id = ? "
        f"AND (normalized_name = ? OR canonical_name = ?){clause}"
        " ORDER BY unit",
        params,
    ).fetchall()
    if not rows:
        suffix = "" if unit is None else f" ({unit or 'no unit'})"
        raise GroceryError(f"item not found on {store['name']} list: {name}{suffix}")
    if len(rows) == 1:
        return rows[0], None
    choices = ", ".join(row["unit"] or "(no unit)" for row in rows)
    if not assume:
        raise GroceryError(
            f"{name} matches {len(rows)} items on the {store['name']} list; "
            f"pass --unit to choose one of: {choices}"
        )
    # Two stable sorts: newest first, then still-needed wins outright.
    ranked = sorted(rows, key=lambda r: r["updated_at"], reverse=True)
    ranked.sort(key=lambda r: r["status"] != "needed")
    chosen = ranked[0]
    return chosen, {
        "kind": "item_ambiguous",
        "name": name,
        "matches": len(rows),
        "choices": choices,
        "chosen": chosen["unit"] or "",
    }


_PARENTHETICAL = re.compile(r"\(([^()]*)\)")
# Same connector words synonyms.py drops, so "papel de toalha" and "papel
# toalha" tokenize alike.
_CONNECTORS = frozenset(
    "de da do das dos d e em no na nos nas com sem ao aos a o os as um uma "
    "para pra of the and in for with to".split()
)


def _match_tokens(name: str) -> list[str]:
    """Whole tokens, folded (case and accents gone), connectors and bare
    numbers dropped."""
    return [t for t in synonyms.fold(name).split()
            if t not in _CONNECTORS and not t.isdigit()]


def _same_token(a: str, b: str) -> bool:
    # canonical() on one word covers plurals and the curated EN/PT pairs
    # ("bananas"/"banana", "milk"/"leite") without matching inside words.
    return a == b or synonyms.same_product(a, b)


def _same_run(tokens: list[str], spoken: list[str]) -> bool:
    return len(tokens) == len(spoken) and all(map(_same_token, tokens, spoken))


def _contains_run(tokens: list[str], spoken: list[str]) -> bool:
    size = len(spoken)
    return any(_same_run(tokens[i:i + size], spoken)
               for i in range(len(tokens) - size + 1))


def partial_candidates(
    conn: sqlite3.Connection,
    store: sqlite3.Row,
    name: str,
    unit: str | None = None,
) -> list[tuple[sqlite3.Row, bool]]:
    """Pending items a name that matched nothing exactly might mean.

    Only reached once exact and cross-language lookup have failed, and only for
    rows still needed in this store (the store row is already the caller's
    household). A row is a candidate when any of these holds:

    - the name matches the item with its parentheticals removed, by
      canonical key ("papel toalha" ~ "Paper towels (Bounty)");
    - the name is a parenthetical part, usually the brand
      ("Bounty" ~ "Paper towels (Bounty)");
    - the name is a whole-token run inside the item's name, which includes it
      being the head noun ("bounty" ~ "Bounty paper towels").

    Each candidate carries a `guarded` flag. It is set when the name is itself a
    curated product (synonyms.group_for) whose canonical key differs from the
    item's: "leite" inside "Leite condensado", "morango" inside
    "Iogurte (morango)". That is the synonyms module's whole-name rule, and it
    wins: a guarded candidate is never picked silently.
    """
    spoken = _match_tokens(name)
    if not spoken:
        return []
    key = synonyms.canonical(name)
    known = synonyms.group_for(name) is not None
    clause, params = "", [store["id"]]
    if unit is not None:
        clause = " AND unit = ?"
        params.append(unit)
    rows = conn.execute(
        f"SELECT * FROM items WHERE store_id = ? AND status = 'needed'{clause}"
        " ORDER BY normalized_name, unit",
        params,
    ).fetchall()
    found: list[tuple[sqlite3.Row, bool]] = []
    for row in rows:
        base = _PARENTHETICAL.sub(" ", row["name"])
        same = bool(_match_tokens(base)) and synonyms.canonical(base) == key
        brand = any(_same_run(_match_tokens(part), spoken)
                    for part in _PARENTHETICAL.findall(row["name"]))
        if same or brand or _contains_run(_match_tokens(row["name"]), spoken):
            found.append((row, known and not same))
    return found


def set_status(
    conn: sqlite3.Connection,
    store: str,
    names: Iterable[str],
    status: str,
    unit: str | None = None,
    actor: str | None = None,
    group_id: int | None = None,
    source_type: str = "",
    source_ref: str = "",
    raw_text: str = "",
) -> dict[str, Any]:
    begin_write(conn)      # buying an unlisted item inserts it: see begin_write
    store = store_row(conn, store, group_id=group_id)
    changed: list[str] = []
    assumptions: list[dict] = []
    ambiguous: list[dict] = []
    stamp = now_iso()
    source = dict(source_type=source_type, source_ref=source_ref, raw_text=raw_text)
    for name in names:
        try:
            row, assumption = resolve_item(conn, store, name, unit, assume=True)
        except GroceryError:
            if status != "purchased":
                raise
            clean = display_name(name)
            # Before calling it unlisted, see whether it names a pending item
            # in part: "Bounty" for "Paper towels (Bounty)".
            candidates = partial_candidates(conn, store, name, unit)
            choices = list(dict.fromkeys(r["name"] for r, _ in candidates))
            if len(choices) == 1 and not any(guarded for _, guarded in candidates):
                rows = sorted((r for r, _ in candidates),
                              key=lambda r: r["updated_at"], reverse=True)
                row, assumption = rows[0], None
                assumptions.append({"kind": "item_partial_match", "name": clean,
                                    "item": row["name"]})
                if len(rows) > 1:
                    assumption = {
                        "kind": "item_ambiguous", "name": row["name"],
                        "matches": len(rows),
                        "choices": ", ".join(r["unit"] or "(no unit)" for r in rows),
                        "chosen": row["unit"] or "",
                    }
            elif choices:
                # Several items, or one a guard says is a different product
                # ("leite" vs "Leite condensado"): change nothing and hand the
                # choice back rather than guess or invent an unlisted row.
                ambiguous.append({"name": clean, "candidates": choices})
                assumptions.append({"kind": "item_choice_needed", "name": clean,
                                    "choices": ", ".join(choices)})
                continue
            else:
                # Marking something bought that was never listed is the shopper
                # standing in the aisle holding it. Record it rather than refusing.
                cursor = conn.execute(
                    """
                    INSERT INTO items(
                        store_id, name, normalized_name, canonical_name, quantity,
                        unit, note, status, first_added_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, ?, '', 'purchased', ?, ?)
                    """,
                    (store["id"], clean, normalized(clean), synonyms.canonical(clean),
                     unit or "", stamp, stamp),
                )
                if source_type:
                    # Created here rather than by add, so its provenance lands here.
                    conn.execute(
                        """
                        INSERT INTO item_sources(item_id, source_type, source_ref, raw_text, observed_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (cursor.lastrowid, source_type, source_ref, raw_text, stamp),
                    )
                for action in ("added", "purchased"):
                    record_event(
                        conn, occurred_at=stamp, store=store["name"], name=clean,
                        action=action, quantity=1.0, unit=unit or "", actor=actor,
                        group_id=store["group_id"], **source,
                    )
                changed.append(clean)
                assumptions.append({"kind": "item_not_listed", "name": clean})
                continue
        conn.execute(
            "UPDATE items SET status = ?, updated_at = ? WHERE id = ?",
            (status, stamp, row["id"]),
        )
        record_event(
            conn, occurred_at=stamp, store=store["name"], name=row["name"],
            action="purchased" if status == "purchased" else "unpurchased",
            quantity=float(row["quantity"]), unit=row["unit"], note=row["note"],
            product_url=row["product_url"],
            actor=actor, group_id=store["group_id"], **source,
        )
        changed.append(row["name"])
        if assumption:
            assumptions.append(assumption)
    conn.commit()
    # `ambiguous` lists names that changed nothing because they could mean
    # more than one pending item; the caller asks which.
    return {"items": changed, "assumptions": assumptions, "ambiguous": ambiguous}


def remove_items(
    conn: sqlite3.Connection,
    store: str,
    names: Iterable[str],
    unit: str | None = None,
    actor: str | None = None,
    group_id: int | None = None,
    source_type: str = "",
    source_ref: str = "",
    raw_text: str = "",
) -> list[str]:
    begin_write(conn)
    store = store_row(conn, store, group_id=group_id)
    removed: list[str] = []
    stamp = now_iso()
    for name in names:
        row, _ = resolve_item(conn, store, name, unit)
        conn.execute("DELETE FROM items WHERE id = ?", (row["id"],))
        record_event(
            conn, occurred_at=stamp, store=store["name"], name=row["name"],
            action="removed", quantity=float(row["quantity"]), unit=row["unit"],
            note=row["note"], product_url=row["product_url"], actor=actor, group_id=store["group_id"],
            source_type=source_type, source_ref=source_ref, raw_text=raw_text,
        )
        removed.append(row["name"])
    conn.commit()
    return removed
