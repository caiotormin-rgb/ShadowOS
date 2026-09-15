"""Argument parsing and command dispatch."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable

import activity
import sections
from catalog import remember_section, section_for
from contacts import add_contact, mark_delivered, prepare_share, remove_contact
from db import DEFAULT_DB, connect
from errors import GroceryError
from items import (
    current_items, ingest_items, parse_items_json, remove_items, set_status,
)
from people import language_for, public_label, remember_person, set_timezone
from groups import add_member, group_row, members, remove_member, group_for_actor
import guide
import roster
from insights import due, purchase_history, typical_interval_days
from render import (
    confirm, confirm_items, grouped_items, last_touched, phrase, render_assumptions,
    render_due,
    render_grouped, render_stamp,
)
from stores import resolve_store, store_row
from text import display_name, normalized, unit_filter, validate_timestamp
from trips import close_trip, reopen_trip


def emit_ingest(result: dict, assumed: dict | None, args, lang: str) -> None:
    """Confirm an add without replaying the list back at the reader."""
    notes = render_assumptions([assumed] if assumed else [], lang)
    if args.format != "text":
        json_print({**result, "assumptions": notes})
        return
    lines = []
    if result["added"]:
        lines.append(confirm_items("added", result["added"], lang,
                                   store=result["store"]))
    if result["merged"]:
        lines.append(confirm_items("updated", result["merged"], lang,
                                   store=result["store"]))
    text = " ".join(lines)
    print(f"{text} ({'; '.join(notes)})" if notes else text)


def json_print(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def rows_as_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def add_source_arguments(parser: argparse.ArgumentParser) -> None:
    """Where a change came from, recorded in the event log. Optional here —
    unlike ingest — so an operator at the shell is not made to invent one."""
    parser.add_argument("--source-type", default="",
                        choices=["text", "voice", "image", "video", "url"])
    parser.add_argument("--source-ref", default="")
    parser.add_argument("--raw-text", default="")


def source_of(args) -> dict[str, str]:
    return {"source_type": args.source_type, "source_ref": args.source_ref,
            "raw_text": args.raw_text}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="initialize the database")

    add = sub.add_parser("add", help="add one or more text items")
    add.add_argument("--store", help="defaults to the last store touched")
    add.add_argument("items", nargs="+")
    add.add_argument("--at")
    add.add_argument("--actor", help="who is acting; recorded in the event log")
    add.add_argument("--lang", choices=["en", "pt"],
                              help="overrides the requester's stored preference")
    add.add_argument("--format", choices=["json", "text"], default="json")

    ingest = sub.add_parser("ingest", help="store agent-normalized multimodal items")
    ingest.add_argument("--store", help="defaults to the last store touched")
    ingest.add_argument("--source-type", required=True, choices=["text", "voice", "image", "video", "url"])
    ingest.add_argument("--source-ref", default="")
    ingest.add_argument("--raw-text", default="")
    ingest.add_argument("--items-json", required=True)
    ingest.add_argument("--at")
    ingest.add_argument("--actor", help="who is acting; recorded in the event log")
    ingest.add_argument("--lang", choices=["en", "pt"],
                              help="overrides the requester's stored preference")
    ingest.add_argument("--format", choices=["json", "text"], default="json")

    show = sub.add_parser("list", help="show current items")
    show.add_argument("--store", help="defaults to the last store touched")
    show.add_argument("--needed-only", action="store_true")
    show.add_argument("--actor", help="who is asking; selects the reply language")
    show.add_argument("--format", choices=["json", "text"], default="json")
    show.add_argument("--lang", choices=["en", "pt"],
                              help="overrides the requester's stored preference")

    for name, status, help_text in (
        ("buy", "purchased", "mark items purchased"),
        ("unbuy", "needed", "return purchased items to needed"),
    ):
        action = sub.add_parser(name, help=help_text)
        action.add_argument("--store", help="defaults to the last store touched")
        action.add_argument("items", nargs="+")
        action.add_argument("--unit", help="pick among items sharing a name but differing by unit")
        action.add_argument("--actor", help="who is acting; recorded in the event log")
        action.add_argument("--lang", choices=["en", "pt"],
                              help="overrides the requester's stored preference")
        action.add_argument("--format", choices=["json", "text"], default="json")
        action.set_defaults(item_status=status)
        add_source_arguments(action)

    remove = sub.add_parser("remove", help="remove items without recording a purchase")
    remove.add_argument("--store", help="defaults to the last store touched")
    remove.add_argument("items", nargs="+")
    remove.add_argument("--unit", help="pick among items sharing a name but differing by unit")
    remove.add_argument("--actor", help="who is acting; recorded in the event log")
    remove.add_argument("--lang", choices=["en", "pt"],
                              help="overrides the requester's stored preference")
    remove.add_argument("--format", choices=["json", "text"], default="json")
    add_source_arguments(remove)

    close = sub.add_parser("close", help="archive a trip and roll missing items forward")
    close.add_argument("--store", help="defaults to the last store touched")
    close.add_argument("--at")
    close.add_argument("--actor", help="who is acting; recorded in the event log")
    close.add_argument("--lang", choices=["en", "pt"],
                              help="overrides the requester's stored preference")
    close.add_argument("--format", choices=["json", "text"], default="json")

    section = sub.add_parser("section", help="inspect or teach product sections")
    section_sub = section.add_subparsers(dest="section_command", required=True)
    section_set = section_sub.add_parser("set", help="record the section for a product")
    section_set.add_argument("item")
    section_set.add_argument("section", choices=sections.SECTION_KEYS)
    section_set.add_argument("--source", choices=["agent", "user"], default="user")
    section_show = section_sub.add_parser("show", help="section for a product")
    section_show.add_argument("item")
    section_sub.add_parser("list", help="everything learned so far")
    section_unknown = section_sub.add_parser(
        "unknown", help="listed items with no known section")
    section_unknown.add_argument("--store")
    # Every subcommand needs the caller: the gate runs before the command does.
    for section_parser in (section_set, section_show, section_unknown,
                           section_sub.choices["list"]):
        section_parser.add_argument("--actor", help="who is asking; scopes to their household")

    missing = sub.add_parser(
        "due", help="products usually bought by now that are not on the list")
    missing.add_argument("--store", help="defaults to the last store touched")
    missing.add_argument("--section", choices=sections.SECTION_KEYS)
    missing.add_argument("--actor", help="who is asking; selects the reply language")
    missing.add_argument("--lang", choices=["en", "pt"],
                         help="overrides the requester's stored preference")
    missing.add_argument("--format", choices=["json", "text"], default="json")
    missing.add_argument("--at", help="evaluate as of this time instead of now")
    missing.add_argument("--slack", type=float, default=1.0,
                         help="multiple of the typical interval before something is due")

    stats = sub.add_parser("stats", help="purchase frequency per product")
    stats.add_argument("--store", help="defaults to the last store touched")
    stats.add_argument("--limit", type=int, default=50)
    stats.add_argument("--actor", help="who is asking; scopes to their household")

    group = sub.add_parser("group", help="households and who belongs to them")
    group_sub = group.add_subparsers(dest="group_command", required=True)
    group_add = group_sub.add_parser("add", help="let someone use a list")
    group_add.add_argument("actor", help="how they are identified, e.g. a phone number")
    group_add.add_argument("--group")
    group_add.add_argument("--role", choices=["owner", "member"], default="member")
    group_rm = group_sub.add_parser("remove", help="revoke access")
    group_rm.add_argument("actor")
    group_rm.add_argument("--group")
    group_members = group_sub.add_parser("members", help="who belongs")
    group_members.add_argument("--group")
    group_sub.add_parser("list", help="all groups")
    group_new = group_sub.add_parser("create", help="create a household")
    group_new.add_argument("name")

    roster_cmd = sub.add_parser(
        "members", help="apply the roster config file")
    roster_sub = roster_cmd.add_subparsers(dest="members_command", required=True)
    roster_sync = roster_sub.add_parser("sync", help="bring the database in line with the file")
    roster_sync.add_argument("--file", default=str(roster.DEFAULT_ROSTER))
    roster_sync.add_argument("--prune", action="store_true",
                             help="also revoke anyone no longer in the file")
    roster_show = roster_sub.add_parser("show", help="read the file without applying it")
    roster_show.add_argument("--file", default=str(roster.DEFAULT_ROSTER))

    for name, forced in (("help", None), ("ajuda", "pt")):
        topic = sub.add_parser(name, help="what to say to the list")
        topic.add_argument("--actor", help="who is asking; selects the language")
        topic.add_argument("--lang", choices=["en", "pt"])
        topic.set_defaults(forced_lang=forced, guide="help")

    greet = sub.add_parser("onboard", help="welcome message for a new member")
    greet.add_argument("--actor", help="who is being welcomed")
    greet.add_argument("--lang", choices=["en", "pt"])
    greet.set_defaults(forced_lang=None, guide="onboard")

    who = sub.add_parser("who", help="who uses this list, and their language")
    who_sub = who.add_subparsers(dest="who_command", required=True)
    who_set = who_sub.add_parser("set", help="record someone's preferred language")
    who_set.add_argument("actor")
    who_set.add_argument("--lang", choices=["en", "pt"], required=True)
    who_set.add_argument("--name", default="")
    who_set.add_argument("--store", help="the store this person usually means")
    who_tz = who_sub.add_parser(
        "timezone", help="override the zone guessed from someone's country code")
    who_tz.add_argument("actor")
    who_tz.add_argument("zone", help="an IANA zone such as America/Sao_Paulo, or 'auto'")
    who_sub.add_parser("list", help="everyone on file")

    layout = sub.add_parser("layout", help="show or set a store's walk order")
    layout.add_argument("--store")
    layout.add_argument("--set", dest="new_layout", choices=sorted(sections.LAYOUTS))
    layout.add_argument("--actor", help="who is asking; scopes to their household")

    reopen = sub.add_parser("reopen", help="undo a close and restore the trip")
    reopen.add_argument("--store", help="defaults to the last store touched")
    reopen.add_argument("--trip-id", type=int, help="defaults to the most recent trip")
    reopen.add_argument("--actor", help="who is acting; recorded in the event log")
    reopen.add_argument("--lang", choices=["en", "pt"],
                              help="overrides the requester's stored preference")
    reopen.add_argument("--format", choices=["json", "text"], default="json")

    events = sub.add_parser("events", help="read the append-only history")
    events.add_argument("--store")
    events.add_argument("--item")
    events.add_argument("--action")
    events.add_argument("--actor", help="who is asking; scopes to their household")
    events.add_argument("--by", help="only events by this person")
    events.add_argument("--limit", type=int, default=50)
    events.add_argument("--lang", choices=["en", "pt"])

    feed = sub.add_parser(
        "activity", help="who changed what on the list, and when (member-facing)")
    feed.add_argument("--actor", required=True,
                      help="who is asking; sets the household, language and clock")
    feed.add_argument("--since", help="today, yesterday, 12h, 7d, 2w, or YYYY-MM-DD")
    feed.add_argument("--until", help="same forms; a day includes the whole day")
    feed.add_argument("--by", help="a household member's name, or their number")
    feed.add_argument("--item", help="matches either language and qualified names")
    feed.add_argument("--store")
    feed.add_argument("--action", choices=list(activity.ACTIONS))
    feed.add_argument("--limit", type=int, default=activity.DEFAULT_LIMIT,
                      help=f"entries to show, at most {activity.MAX_LIMIT}")
    feed.add_argument("--lang", choices=["en", "pt"])
    feed.add_argument("--format", choices=["json", "text"], default="json",
                      help="json carries the rendered text under `text`")
    feed.add_argument("--at", help="resolve relative dates as of this time instead of now")

    history = sub.add_parser("history", help="show archived trips")
    history.add_argument("--store", help="defaults to the last store touched")
    history.add_argument("--limit", type=int, default=10)
    history.add_argument("--actor", help="who is asking; scopes to their household")
    history.add_argument("--lang", choices=["en", "pt"])

    stores = sub.add_parser("stores", help="list stores")
    stores.add_argument("--actor", help="who is asking; scopes to their household")
    stores.add_argument("--lang", choices=["en", "pt"])

    allow = sub.add_parser("allow", help="manage the external sharing allowlist")
    allow_sub = allow.add_subparsers(dest="allow_command", required=True)
    allow_add = allow_sub.add_parser("add")
    allow_add.add_argument("alias")
    allow_add.add_argument("--channel", required=True)
    allow_add.add_argument("--target", required=True)
    allow_remove = allow_sub.add_parser("remove")
    allow_remove.add_argument("alias")
    allow_sub.add_parser("list")

    share = sub.add_parser("share", help="render a list for an allowlisted contact; does not send")
    share.add_argument("--store", help="defaults to the last store touched")
    share.add_argument("--contact", required=True)
    share.add_argument("--actor", help="who is asking; scopes to their household")

    delivered = sub.add_parser("delivered", help="record successful external delivery")
    delivered.add_argument("share_id", type=int)
    delivered.add_argument("--actor", help="who is asking; scopes to their household")
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with connect(args.db) as conn:
        # One resolution for the whole command: an explicit --lang, else the
        # requester's stored preference, else the configured default.
        lang = language_for(conn, getattr(args, "actor", None),
                            getattr(args, "lang", None))
        # Who the caller is decides which lists exist for them at all. Group
        # membership is checked before any store is resolved, so a non-member
        # cannot even discover another household's stores.
        caller_group = None
        if args.command not in {"init", "group", "who", "allow", "members",
                                "help", "ajuda", "onboard"}:
            caller_group = group_for_actor(conn, getattr(args, "actor", None))["id"]
        if args.command == "init":
            json_print({"database": str(Path(args.db).expanduser().resolve()), "initialized": True})
        elif args.command == "add":
            store_name, assumed = resolve_store(conn, args.store, caller_group, getattr(args, 'actor', None))
            items = parse_items_json(json.dumps(args.items))
            result = ingest_items(
                conn, store_name, items, "text", raw_text="; ".join(args.items),
                observed_at=args.at, actor=args.actor, group_id=caller_group,
            )
            emit_ingest(result, assumed, args, lang)
        elif args.command == "ingest":
            store_name, assumed = resolve_store(conn, args.store, caller_group, getattr(args, 'actor', None))
            items = parse_items_json(args.items_json)
            result = ingest_items(
                conn, store_name, items, args.source_type, args.source_ref,
                args.raw_text, args.at, args.actor, caller_group,
            )
            emit_ingest(result, assumed, args, lang)
        elif args.command == "list":
            store_name, assumed = resolve_store(conn, args.store, caller_group, getattr(args, 'actor', None))
            if args.format == "text":
                store, groups = grouped_items(conn, store_name, False, caller_group)
                bought = [] if args.needed_only else [
                    r for r in current_items(conn, store_name, True, caller_group)[1]
                    if r["status"] == "purchased"
                ]
                stamp = render_stamp(last_touched(conn, store["name"], caller_group), lang)
                conn.commit()
                print(render_grouped(store, groups, lang, bought, stamp))
            else:
                store, rows = current_items(conn, store_name, not args.needed_only, caller_group)
                conn.commit()
                json_print({
                    "store": store["name"], "layout": store["layout"],
                    "items": rows_as_dicts(rows),
                    "assumptions": render_assumptions([assumed] if assumed else [], lang),
                })
        elif args.command in {"buy", "unbuy"}:
            store_name, assumed = resolve_store(conn, args.store, caller_group, getattr(args, 'actor', None))
            result = set_status(
                conn, store_name, args.items, args.item_status,
                unit_filter(args.unit), args.actor, caller_group, **source_of(args),
            )
            if assumed:
                result["assumptions"].insert(0, assumed)
            if args.format == "text":
                remaining = len([
                    r for r in current_items(conn, store_name, False, caller_group)[1]
                ])
                print(confirm_items(
                    "purchased" if args.item_status == "purchased" else "needed",
                    result["items"], lang,
                    render_assumptions(result["assumptions"], lang),
                    phrase("remaining", lang, count=remaining),
                    store=store_name,
                ))
            else:
                result["assumptions"] = render_assumptions(result["assumptions"], lang)
                json_print({"store": store_name, "status": args.item_status, **result})
        elif args.command == "remove":
            store_name, assumed = resolve_store(conn, args.store, caller_group, getattr(args, 'actor', None))
            removed = remove_items(conn, store_name, args.items, unit_filter(args.unit),
                                   args.actor, caller_group, **source_of(args))
            if args.format == "text":
                print(confirm_items("removed", removed, lang,
                                    render_assumptions([assumed] if assumed else [], lang),
                                    store=store_name))
            else:
                json_print({
                    "store": store_name, "removed": removed,
                    "assumptions": render_assumptions([assumed] if assumed else [], lang),
                })
        elif args.command == "close":
            store_name, assumed = resolve_store(conn, args.store, caller_group, getattr(args, 'actor', None))
            result = close_trip(conn, store_name, args.at, args.actor, caller_group)
            if args.format == "text":
                if result["duplicate"]:
                    print(confirm("duplicate", lang, store=store_name))
                else:
                    print(confirm(
                        "closed", lang,
                        render_assumptions([assumed] if assumed else [], lang),
                        phrase("undo", lang),
                        store=store_name, bought=len(result["purchased"]),
                        carried=len(result["carried_forward"]),
                    ))
            else:
                json_print({**result,
                            "assumptions": render_assumptions([assumed] if assumed else [], lang)})
        elif args.command == "section":
            if args.section_command == "set":
                json_print({
                    "item": display_name(args.item),
                    "section": remember_section(conn, args.item, args.section, args.source),
                })
                conn.commit()
            elif args.section_command == "show":
                found = section_for(conn, args.item)
                conn.commit()
                json_print({
                    "item": display_name(args.item), "section": found,
                    "label": sections.label(found) if found else None,
                })
            elif args.section_command == "unknown":
                store_name, _ = resolve_store(conn, args.store, caller_group, getattr(args, 'actor', None))
                _, rows = current_items(conn, store_name, True, caller_group)
                unknown = [r["name"] for r in rows if section_for(conn, r["name"]) is None]
                conn.commit()
                json_print({"store": store_name, "unclassified": unknown})
            else:
                json_print(rows_as_dicts(conn.execute(
                    "SELECT * FROM product_sections ORDER BY section, normalized_name"
                ).fetchall()))
        elif args.command == "due":
            store_name, _ = resolve_store(conn, args.store, caller_group, getattr(args, 'actor', None))
            rows = due(conn, store_name, args.section, args.at, slack=args.slack,
                       group_id=caller_group)
            conn.commit()
            if args.format == "text":
                print(render_due(store_name, rows, lang, args.section))
            else:
                json_print({"store": store_name, "section": args.section, "due": rows})
        elif args.command == "stats":
            store_name, _ = resolve_store(conn, args.store, caller_group, getattr(args, 'actor', None))
            rows = purchase_history(conn, store_name, args.limit, caller_group)
            json_print({"store": store_name, "products": [
                {**r, "typical_interval_days": typical_interval_days(r)} for r in rows
            ]})
        elif args.command == "group":
            if args.group_command == "add":
                json_print(add_member(conn, args.group, args.actor, args.role))
            elif args.group_command == "remove":
                json_print({"removed": remove_member(conn, args.group, args.actor),
                            "group": group_row(conn, args.group)["name"]})
            elif args.group_command == "members":
                json_print({"group": group_row(conn, args.group)["name"],
                            "members": members(conn, args.group)})
            elif args.group_command == "create":
                row = group_row(conn, args.name, create=True)
                json_print({"group": row["name"], "created_at": row["created_at"]})
            else:
                json_print(rows_as_dicts(conn.execute(
                    "SELECT * FROM groups ORDER BY id").fetchall()))
        elif args.command in {"help", "ajuda", "onboard"}:
            # Typing "ajuda" is itself the language signal, unless overridden.
            chosen = args.lang or args.forced_lang or lang
            group_id = None
            if args.actor:
                try:
                    group_id = group_for_actor(conn, args.actor)["id"]
                except GroceryError:
                    group_id = None
            renderer = guide.help_text if args.guide == "help" else guide.onboarding
            print(renderer(conn, args.actor, chosen, group_id))
        elif args.command == "members":
            if args.members_command == "sync":
                json_print(roster.apply(conn, args.file, args.prune))
            else:
                json_print(roster.load(args.file))
        elif args.command == "who":
            if args.who_command == "set":
                # Here --lang is the value being stored, not the reply language.
                json_print(remember_person(conn, args.actor, args.lang, args.name, args.store))
            elif args.who_command == "timezone":
                json_print(set_timezone(conn, args.actor, args.zone))
            else:
                json_print(rows_as_dicts(conn.execute(
                    "SELECT * FROM people ORDER BY actor").fetchall()))
        elif args.command == "layout":
            store_name, _ = resolve_store(conn, args.store, caller_group, getattr(args, 'actor', None))
            store = store_row(conn, store_name, group_id=caller_group)
            if args.new_layout:
                conn.execute("UPDATE stores SET layout = ? WHERE id = ?",
                             (args.new_layout, store["id"]))
                conn.commit()
            store = store_row(conn, store_name, group_id=caller_group)
            json_print({
                "store": store["name"], "layout": store["layout"],
                "order": list(sections.layout_order(store["layout"])),
                "available": sorted(sections.LAYOUTS),
            })
        elif args.command == "reopen":
            store_name, assumed = resolve_store(conn, args.store, caller_group, getattr(args, 'actor', None))
            result = reopen_trip(conn, store_name, args.trip_id, args.actor, caller_group)
            if args.format == "text":
                print(confirm("reopened", lang,
                              render_assumptions([assumed] if assumed else [], lang),
                              store=store_name, count=len(result["restored"])))
            else:
                json_print({**result,
                            "assumptions": render_assumptions([assumed] if assumed else [], lang)})
        elif args.command == "events":
            clauses, params = [], []
            if args.store:
                clauses.append("store = ?")
                params.append(display_name(args.store))
            if args.item:
                clauses.append("normalized_name = ?")
                params.append(normalized(args.item))
            if args.action:
                clauses.append("action = ?")
                params.append(args.action)
            # --actor says who is asking; --by filters whose events to show.
            # One flag doing both meant a member could only ever see their own
            # history, and never answer "who put this on the list?".
            if args.by:
                clauses.append("actor = ?")
                params.append(args.by)
            clauses.append("group_id = ?")
            params.append(caller_group)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            params.append(args.limit)
            json_print(rows_as_dicts(conn.execute(
                f"SELECT * FROM events {where} ORDER BY occurred_at DESC, id DESC LIMIT ?",
                params,
            ).fetchall()))
        elif args.command == "activity":
            result = activity.report(
                conn, args.actor, caller_group, lang,
                since=args.since, until=args.until, by=args.by, item=args.item,
                store=args.store, action=args.action, limit=args.limit,
                now=validate_timestamp(args.at) if args.at else None,
            )
            if args.format == "text":
                print(result["text"])
            else:
                json_print(result)
        elif args.command == "history":
            store_name, _ = resolve_store(conn, args.store, caller_group, getattr(args, 'actor', None))
            store = store_row(conn, store_name, group_id=caller_group)
            trips = conn.execute(
                "SELECT * FROM trips WHERE store_id = ? ORDER BY closed_at DESC LIMIT ?",
                (store["id"], args.limit),
            ).fetchall()
            result = []
            for trip in trips:
                result.append({
                    **dict(trip),
                    # Members read this: a name, never the closer's number.
                    "closed_by": public_label(conn, trip["closed_by"]) or None,
                    "items": rows_as_dicts(conn.execute(
                        "SELECT name, quantity, unit, note, outcome, first_added_at FROM trip_items WHERE trip_id = ? ORDER BY outcome, name",
                        (trip["id"],),
                    ).fetchall()),
                })
            json_print({"store": store["name"], "trips": result})
        elif args.command == "stores":
            json_print(rows_as_dicts(conn.execute(
                "SELECT * FROM stores WHERE group_id = ? ORDER BY normalized_name",
                (caller_group,),
            ).fetchall()))
        elif args.command == "allow":
            if args.allow_command == "add":
                json_print(add_contact(conn, args.alias, args.channel, args.target))
            elif args.allow_command == "remove":
                json_print({"removed": remove_contact(conn, args.alias)})
            else:
                json_print(rows_as_dicts(conn.execute(
                    "SELECT alias, channel, target, added_at FROM contacts WHERE active = 1 ORDER BY normalized_alias"
                ).fetchall()))
        elif args.command == "share":
            store_name, _ = resolve_store(conn, args.store, caller_group, getattr(args, 'actor', None))
            json_print(prepare_share(conn, store_name, args.contact, caller_group))
        elif args.command == "delivered":
            json_print(mark_delivered(conn, args.share_id, caller_group))
    return 0


def main() -> None:
    try:
        raise SystemExit(run())
    except GroceryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
