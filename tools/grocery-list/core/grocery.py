"""Local grocery-list state and policy engine for an OpenClaw workflow.

This module is a façade. The implementation lives in focused modules — see
`db`, `text`, `stores`, `catalog`, `items`, `trips`, `render`, `contacts`, and
`cli` — while every name the CLI, the tests, the web harness, and the installed
skill already import stays importable from here.
"""

from __future__ import annotations

from catalog import remember_section, section_for
from cli import build_parser, json_print, main, rows_as_dicts, run
from contacts import add_contact, mark_delivered, prepare_share, remove_contact
from db import (
    APP_DIR, DEFAULT_DB, DEV_DB, connect, ensure_column, record_event,
)
from errors import GroceryError
from people import DEFAULT_LANG, language_for, remember_person
from groups import (
    add_member, group_for_actor, group_row, members, remove_member,
)
from insights import due, purchase_history, typical_interval_days
from items import (
    current_items, ingest_items, normalize_product_url, parse_items_json, remove_items, resolve_item,
    set_status,
)
from render import (
    grouped_items, item_line, render_due, render_grouped, render_list,
)
from stores import DEFAULT_STORE, resolve_store, store_row
from text import (
    DEFAULT_TZ, clean_text, display_name, normalized, now_iso, parse_timestamp,
    quantity_text, unit_filter, validate_timestamp,
)
from trips import CLOSE_WINDOW_SECONDS, close_trip, reopen_trip

__all__ = [
    "APP_DIR", "CLOSE_WINDOW_SECONDS", "DEFAULT_DB", "DEFAULT_STORE",
    "DEFAULT_TZ", "DEV_DB", "GroceryError", "add_contact", "add_member", "build_parser",
    "clean_text", "close_trip", "connect", "current_items", "display_name", "due",
    "ensure_column", "group_for_actor", "group_row", "grouped_items", "ingest_items", "item_line", "json_print",
    "DEFAULT_LANG", "language_for", "main", "mark_delivered", "members", "normalized", "normalize_product_url", "now_iso", "parse_items_json",
    "parse_timestamp", "prepare_share", "purchase_history", "quantity_text", "record_event",
    "remember_person", "remember_section", "remove_contact", "remove_items", "remove_member", "render_due", "render_grouped",
    "render_list", "reopen_trip", "resolve_item", "resolve_store",
    "rows_as_dicts", "run", "section_for", "set_status", "store_row",
    "typical_interval_days", "unit_filter", "validate_timestamp",
]


if __name__ == "__main__":
    main()
