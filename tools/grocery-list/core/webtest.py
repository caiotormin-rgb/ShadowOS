#!/usr/bin/env python3
"""Localhost-only browser harness for exercising grocery.py by hand.

This is a test tool, not a product surface: it binds to 127.0.0.1 only, has no
authentication, and calls the engine functions in ``grocery`` directly instead
of shelling out to the CLI. Standard library only, like the rest of the repo.

    python3 webtest.py --port 8765          # dev database
    python3 webtest.py --db data/grocery.sqlite3   # the live list, deliberately
"""

from __future__ import annotations

import argparse
import html
import json
import sqlite3
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import grocery
import render
import sections


HOST = "127.0.0.1"  # never 0.0.0.0: this tool must not be reachable off-box
DEFAULT_PORT = 8765
MAX_BODY = 1 << 20  # 1 MiB is far more than any form here needs

e = html.escape


CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0 auto; padding: 1.5rem 1.25rem 4rem; max-width: 62rem;
  font: 15px/1.45 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  color: #1c1c1e; background: #fbfbfa;
}
h1 { font-size: 1.35rem; margin: 0 0 .15rem; }
h2 { font-size: 1.05rem; margin: 1.6rem 0 .5rem; }
h3.section { margin: 16px 0 5px; font-size: .78rem; text-transform: uppercase;
  letter-spacing: .07em; opacity: .7; border-bottom: 1px solid currentColor;
  padding-bottom: 3px; }
h3.section .count { float: right; opacity: .7; font-weight: normal; }
h2 .stamp { font-weight: normal; font-size: .78rem; opacity: .65; margin-left: 10px; }
form.inline { display: inline-block; margin-left: 14px; font-weight: normal;
  font-size: .82rem; }
.sub { color: #6b6b70; font-size: .82rem; margin: 0 0 1.1rem; }
.sub code { background: #ececec; padding: .1rem .3rem; border-radius: 3px; }
a { color: #0b5cad; }
.bar {
  display: flex; flex-wrap: wrap; gap: .75rem 1.25rem; align-items: flex-end;
  padding: .85rem 1rem; margin-bottom: 1rem;
  background: #fff; border: 1px solid #e0e0e0; border-radius: 8px;
}
.bar form { display: flex; gap: .4rem; align-items: flex-end; }
label { display: block; font-size: .72rem; text-transform: uppercase;
        letter-spacing: .04em; color: #6b6b70; margin-bottom: .2rem; }
input[type=text], select, textarea {
  font: inherit; padding: .35rem .5rem; border: 1px solid #c8c8cc;
  border-radius: 5px; background: #fff; color: inherit;
}
textarea { width: 100%; min-height: 6.5rem; font-family: ui-monospace, Menlo, Consolas, monospace; font-size: .88rem; }
button {
  font: inherit; padding: .35rem .7rem; border: 1px solid #c8c8cc;
  border-radius: 5px; background: #f2f2f4; color: inherit; cursor: pointer;
}
button:hover { background: #e6e6ea; }
button.primary { background: #0b5cad; border-color: #0b5cad; color: #fff; }
button.primary:hover { background: #0a4f95; }
button.link { border: none; background: none; padding: .15rem .3rem;
              color: #0b5cad; text-decoration: underline; }
button.danger { color: #a3231b; }
table { width: 100%; border-collapse: collapse; background: #fff;
        border: 1px solid #e0e0e0; border-radius: 8px; }
th, td { text-align: left; padding: .45rem .6rem; border-bottom: 1px solid #eee;
         font-size: .9rem; vertical-align: top; }
th { font-size: .72rem; text-transform: uppercase; letter-spacing: .04em;
     color: #6b6b70; background: #fafafa; }
tr:last-child td { border-bottom: none; }
td.actions { white-space: nowrap; text-align: right; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.purchased td.name { text-decoration: line-through; color: #8a8a8f; }
.empty { color: #8a8a8f; font-style: italic; padding: .6rem .1rem; }
.banner { padding: .6rem .8rem; border-radius: 6px; margin-bottom: 1rem;
          border: 1px solid; font-size: .9rem; }
.banner.error { background: #fdecea; border-color: #f0b7b1; color: #8c1d13; }
.banner.notice { background: #e9f5ec; border-color: #b6ddc1; color: #1c5c2e; }
.banner.assume { background: #fdf4e3; border-color: #ecd7a6; color: #7a5410; }
.banner ul { margin: .3rem 0 0; padding-left: 1.1rem; }
.card { background: #fff; border: 1px solid #e0e0e0; border-radius: 8px;
        padding: .9rem 1rem; margin-top: .5rem; }
.hint { color: #6b6b70; font-size: .8rem; margin: .4rem 0 .6rem; }
.hint code { background: #ececec; padding: .1rem .3rem; border-radius: 3px; }
.row-actions { display: inline; }
.pill { display: inline-block; padding: .05rem .4rem; border-radius: 10px;
        background: #ececef; font-size: .78rem; }
@media (prefers-color-scheme: dark) {
  body { color: #e8e8ea; background: #16161a; }
  .bar, table, .card { background: #202026; border-color: #33333c; }
  th { background: #26262e; color: #a0a0aa; }
  th, td { border-color: #2c2c34; }
  input[type=text], select, textarea { background: #16161a; border-color: #3a3a44; }
  button { background: #2c2c34; border-color: #3a3a44; }
  button:hover { background: #35353f; }
  button.link { background: none; color: #6fb2f2; }
  a, button.link { color: #6fb2f2; }
  .sub, .hint, .empty { color: #9a9aa4; }
  .sub code, .hint code, .pill { background: #2c2c34; }
  .banner.error { background: #3a1c1a; border-color: #6b2b25; color: #ffb4ac; }
  .banner.notice { background: #1a3324; border-color: #2c5c3c; color: #a8e0ba; }
  .banner.assume { background: #33280f; border-color: #5e4c1e; color: #e8cd8d; }
}
"""


# --------------------------------------------------------------------------
# input helpers
# --------------------------------------------------------------------------

def parse_item_lines(text: str) -> list[dict[str, Any]]:
    """Turn the free-text box into items, via grocery's own parser.

    One item per line. Optional pipe-separated fields let a tester reach the
    quantity/unit/note columns (and the same-name-different-unit path):

        milk
        milk | 2 | gal | 2%
    """
    entries: list[Any] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split("|")]
        entry: dict[str, Any] = {"name": parts[0]}
        if len(parts) > 1 and parts[1]:
            entry["quantity"] = parts[1]
        if len(parts) > 2:
            entry["unit"] = parts[2]
        if len(parts) > 3:
            entry["note"] = " | ".join(parts[3:])
        entries.append(entry)
    # parse_items_json owns every validation rule, including "at least one".
    return grocery.parse_items_json(json.dumps(entries))


def flatten(query: dict[str, list[str]]) -> dict[str, str]:
    return {key: values[0] for key, values in query.items() if values}


def link(path: str, **params: str | None) -> str:
    clean = {key: value for key, value in params.items() if value not in (None, "")}
    return f"{path}?{urlencode(clean)}" if clean else path


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def page(title: str, body: str) -> str:
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{e(title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n"
        f"{body}\n</body>\n</html>\n"
    )


def hidden(**fields: str) -> str:
    return "".join(
        f'<input type="hidden" name="{e(key)}" value="{e(value)}">'
        for key, value in fields.items()
        if value is not None
    )


ASSUMPTION_SEP = "\n"


def join_assumptions(*parts: dict | str | None) -> str:
    """Render structured assumptions into one redirect-safe query value.

    The engine reports assumptions as records, not prose, so language lives
    here rather than in the state layer.
    """
    rendered = [
        render.render_assumption(part, "en") if isinstance(part, dict) else part
        for part in parts if part
    ]
    return "\n".join(r for r in rendered if r)


def banners(error: str, notice: str, assumed: str = "") -> str:
    """Error, confirmation, and the engine's stated assumptions.

    Assumptions are the visible half of "assume and state instead of asking";
    they arrive newline-joined in the redirect query.
    """
    out = []
    if error:
        out.append(f'<div class="banner error"><strong>Error:</strong> {e(error)}</div>')
    if notice:
        out.append(f'<div class="banner notice">{e(notice)}</div>')
    lines = [line for line in assumed.split(ASSUMPTION_SEP) if line.strip()]
    if lines:
        items = "".join(f"<li>{e(line)}</li>" for line in lines)
        out.append(
            '<div class="banner assume"><strong>Assumed:</strong>'
            f"<ul>{items}</ul></div>"
        )
    return "".join(out)


def toolbar(conn: sqlite3.Connection, store: str, actor: str, view: str) -> str:
    stores = conn.execute("SELECT name FROM stores ORDER BY normalized_name").fetchall()
    options = ['<option value="">— pick a store —</option>']
    for row in stores:
        selected = " selected" if grocery.normalized(row["name"]) == grocery.normalized(store or "") else ""
        options.append(f'<option value="{e(row["name"])}"{selected}>{e(row["name"])}</option>')
    other = "/history" if view == "list" else "/"
    other_label = "History" if view == "list" else "List"
    return f"""
<div class="bar">
  <form method="get" action="/">
    <div><label for="store-pick">Store</label>
      <select id="store-pick" name="store">{''.join(options)}</select></div>
    {hidden(actor=actor)}
    <button type="submit">Open</button>
  </form>
  <form method="post" action="/store">
    <div><label for="store-new">New store</label>
      <input id="store-new" type="text" name="store" placeholder="Costco"></div>
    {hidden(actor=actor)}
    <button type="submit">Create</button>
  </form>
  <form method="get" action="{e(other)}">
    <div><label for="actor">Acting as (optional)</label>
      <input id="actor" type="text" name="actor" value="{e(actor)}" placeholder="owner"></div>
    {hidden(store=store)}
    <button type="submit">Go to {e(other_label)}</button>
  </form>
</div>
"""


def item_row(row: sqlite3.Row, store: str, actor: str) -> str:
    unit = row["unit"] or ""
    fields = hidden(store=store, name=row["name"], unit=unit, actor=actor)
    purchased = row["status"] == "purchased"
    toggle_path = "/unbuy" if purchased else "/buy"
    toggle_label = "Unbuy" if purchased else "Buy"
    quantity = grocery.quantity_text(float(row["quantity"]))
    return f"""
  <tr class="{'purchased' if purchased else 'needed'}">
    <td class="name">{e(row["name"])}</td>
    <td class="num">{e(quantity)}</td>
    <td>{e(unit) or '<span class="pill">—</span>'}</td>
    <td>{e(row["note"])}</td>
    <td class="actions">
      <form class="row-actions" method="post" action="{toggle_path}">{fields}
        <button type="submit" class="link">{toggle_label}</button></form>
      <form class="row-actions" method="post" action="/remove">{fields}
        <button type="submit" class="link danger">Remove</button></form>
    </td>
  </tr>"""


def layout_form(store: str, actor: str, current: str) -> str:
    """Switch a store between a supermarket grid and a warehouse-club walk."""
    options = "".join(
        f'<option value="{e(name)}"{" selected" if name == current else ""}>{e(name)}</option>'
        for name in sorted(sections.LAYOUTS)
    )
    return (
        '<form method="post" action="/layout" class="inline">'
        f'<input type="hidden" name="store" value="{e(store)}">'
        f'<input type="hidden" name="actor" value="{e(actor)}">'
        f'<label>walk order <select name="layout">{options}</select></label>'
        '<button type="submit">apply</button></form>'
    )


def item_table(rows: list[sqlite3.Row], store: str, actor: str, empty: str) -> str:
    if not rows:
        return f'<p class="empty">{e(empty)}</p>'
    head = (
        "<table><tr><th>Item</th><th>Qty</th><th>Unit</th><th>Note</th>"
        "<th></th></tr>"
    )
    return head + "".join(item_row(row, store, actor) for row in rows) + "</table>"


def render_list_view(
    conn: sqlite3.Connection,
    store_name: str,
    actor: str,
    error: str = "",
    notice: str = "",
    assumed: str = "",
) -> str:
    store_display = store_name
    body = ["<h1>Grocery list — web test</h1>"]
    body.append(
        '<p class="sub">Local harness over <code>grocery.py</code>. '
        f'Bound to {HOST} only. <a href="{e(link("/history", store=store_name, actor=actor))}">History</a></p>'
    )

    needed: list[sqlite3.Row] = []
    purchased: list[sqlite3.Row] = []
    if store_name:
        try:
            store_row, rows = grocery.current_items(conn, store_name)
            store_display = store_row["name"]
            for row in rows:
                (purchased if row["status"] == "purchased" else needed).append(row)
        except grocery.GroceryError as exc:
            error = error or str(exc)
            store_display = store_name

    body.append(toolbar(conn, store_display if not error else store_name, actor, "list"))
    body.append(banners(error, notice, assumed))

    if not store_name:
        body.append('<p class="empty">Pick or create a store to begin.</p>')
        return page("Grocery web test", "\n".join(body))

    layout = sections.DEFAULT_LAYOUT
    groups: list[tuple[str, list[sqlite3.Row]]] = []
    stamp = ""
    if not error:
        try:
            store_data, groups = grocery.grouped_items(conn, store_display)
            conn.commit()
            layout = store_data["layout"]
            # Scoped to the store's household, as the WhatsApp path is:
            # unscoped, the footer names whoever last touched a same-named
            # store in another household. Inside the try because store_data
            # only exists once the lookup succeeded.
            stamp = render.render_stamp(
                render.last_touched(conn, store_display, store_data["group_id"]),
                "en",
            )
        except grocery.GroceryError:
            groups = []
    body.append(
        f"<h2>Needed at {e(store_display)}"
        + (f'<span class="stamp">{e(stamp)}</span>' if stamp else "")
        + "</h2>"
        + layout_form(store_display, actor, layout)
    )
    if not groups:
        body.append('<p class="empty">Nothing needed right now.</p>')
    for key, rows in groups:
        body.append(
            f'<h3 class="section">{e(sections.label(key, "en"))}'
            f'<span class="count">{len(rows)}</span></h3>'
        )
        body.append(item_table(rows, store_display, actor, ""))
    body.append("<h2>Purchased (this trip)</h2>")
    body.append(item_table(purchased, store_display, actor, "Nothing marked bought yet."))

    # No unit field here on purpose: this is the assume path. An ambiguous name
    # resolves to the row still needed, and a name that is not on the list is
    # added as already purchased. Either way the engine states what it assumed.
    body.append(f"""
<form class="card" method="post" action="/buy">
  {hidden(store=store_display, actor=actor)}
  <label for="quick-buy">Mark bought by name</label>
  <input id="quick-buy" type="text" name="name" placeholder="milk">
  <button type="submit">Mark bought</button>
  <p class="hint">No unit given, so the engine picks: an ambiguous name takes the
     row still needed (most recently touched wins), and a name that is not on the
     list is added as already purchased. Both are reported as assumptions.</p>
</form>""")

    body.append("<h2>Add items</h2>")
    body.append(f"""
<form class="card" method="post" action="/add">
  {hidden(store=store_display, actor=actor)}
  <label for="items">One item per line</label>
  <textarea id="items" name="items" placeholder="milk&#10;eggs | 2 | dozen&#10;bread | 1 |  | whole wheat"></textarea>
  <p class="hint">Optional fields, pipe separated:
     <code>name | quantity | unit | note</code>. Ingested as source type
     <code>text</code>.</p>
  <button type="submit" class="primary">Add to {e(store_display)}</button>
</form>""")

    body.append("<h2>Trip</h2>")
    body.append(f"""
<form class="card" method="post" action="/close">
  {hidden(store=store_display, actor=actor)}
  <p class="hint">Closing archives a dated snapshot, drops purchased items, and
     rolls anything still needed onto the next list.</p>
  <button type="submit">Close the {e(store_display)} trip</button>
</form>""")
    return page(f"Grocery web test — {store_display}", "\n".join(body))


def render_history_view(
    conn: sqlite3.Connection,
    store_name: str,
    actor: str,
    limit: int = 100,
    error: str = "",
    notice: str = "",
    assumed: str = "",
) -> str:
    body = ["<h1>History</h1>"]
    body.append(
        '<p class="sub">The append-only <code>events</code> table, newest first. '
        f'<a href="{e(link("/", store=store_name, actor=actor))}">Back to the list</a></p>'
    )
    body.append(toolbar(conn, store_name, actor, "history"))

    clauses: list[str] = []
    params: list[Any] = []
    if store_name:
        try:
            resolved = grocery.store_row(conn, store_name)["name"]
        except grocery.GroceryError:
            resolved = store_name
        clauses.append("store = ?")
        params.append(resolved)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT id, occurred_at, store, item_name, normalized_name, unit, quantity,
               action, source_type, source_ref, actor, trip_id, note
        FROM events {where}
        ORDER BY occurred_at DESC, id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()

    body.append(banners(error, notice, assumed))
    scope = f" for {store_name}" if store_name else " (all stores)"
    body.append(f"<h2>{e(str(len(rows)))} most recent events{e(scope)}</h2>")
    if not rows:
        body.append('<p class="empty">No events recorded yet.</p>')
        return page("Grocery web test — history", "\n".join(body))

    cells = [
        "<table><tr><th>#</th><th>When</th><th>Store</th><th>Item</th><th>Unit</th>"
        "<th>Qty</th><th>Action</th><th>Source</th><th>Actor</th><th>Trip</th>"
        "<th>Note</th></tr>"
    ]
    for row in rows:
        quantity = "" if row["quantity"] is None else grocery.quantity_text(float(row["quantity"]))
        source = row["source_type"] or ""
        if row["source_ref"]:
            source = f"{source}: {row['source_ref']}" if source else row["source_ref"]
        cells.append(
            "<tr>"
            f"<td class=\"num\">{row['id']}</td>"
            f"<td>{e(row['occurred_at'])}</td>"
            f"<td>{e(row['store'])}</td>"
            f"<td>{e(row['item_name'])}</td>"
            f"<td>{e(row['unit'] or '')}</td>"
            f"<td class=\"num\">{e(quantity)}</td>"
            f"<td><span class=\"pill\">{e(row['action'])}</span></td>"
            f"<td>{e(source)}</td>"
            f"<td>{e(row['actor'] or '')}</td>"
            f"<td class=\"num\">{'' if row['trip_id'] is None else row['trip_id']}</td>"
            f"<td>{e(row['note'] or '')}</td>"
            "</tr>"
        )
    cells.append("</table>")
    body.append("".join(cells))
    return page("Grocery web test — history", "\n".join(body))


# --------------------------------------------------------------------------
# application
# --------------------------------------------------------------------------

class Response:
    def __init__(
        self,
        status: int = 200,
        body: str = "",
        content_type: str = "text/html; charset=utf-8",
        location: str | None = None,
    ) -> None:
        self.status = status
        self.text = body
        self.body = body.encode("utf-8")
        self.headers: dict[str, str] = {"Content-Type": content_type}
        if location is not None:
            self.headers["Location"] = location

    @property
    def location(self) -> str:
        return self.headers.get("Location", "")


def redirect(path: str, **params: str | None) -> Response:
    target = link(path, **params)
    return Response(303, f'<a href="{e(target)}">{e(target)}</a>', location=target)


class GroceryWebApp:
    """Request routing, decoupled from http.server so tests can call it."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)

    def connect(self) -> sqlite3.Connection:
        # One connection per request: ThreadingHTTPServer hands each request to
        # its own thread, and sqlite3 connections are not shared across threads.
        return grocery.connect(self.db_path)

    # -- GET ---------------------------------------------------------------

    def handle_get(self, path: str, query: dict[str, str]) -> Response:
        store = query.get("store", "")
        actor = query.get("actor", "")
        error = query.get("error", "")
        notice = query.get("notice", "")
        assumed = query.get("assumed", "")
        if path == "/favicon.ico":
            return Response(204, "")
        conn = self.connect()
        try:
            if path == "/":
                if not store:
                    # No store picked: fall back the way the CLI does rather
                    # than making the tester choose. Nothing to fall back on
                    # is not an error here, just the empty picker.
                    try:
                        store, assumption = grocery.resolve_store(conn, None)
                        assumed = join_assumptions(assumed, assumption)
                    except grocery.GroceryError:
                        pass
                return Response(
                    200, render_list_view(conn, store, actor, error, notice, assumed)
                )
            if path == "/history":
                try:
                    limit = max(1, min(1000, int(query.get("limit", "100"))))
                except ValueError:
                    limit = 100
                return Response(
                    200,
                    render_history_view(conn, store, actor, limit, error, notice, assumed),
                )
            return Response(
                404,
                page("Not found", f"<h1>404</h1><p>No route for {e(path)}.</p>"
                                  '<p><a href="/">Back to the list</a></p>'),
            )
        finally:
            conn.close()

    # -- POST --------------------------------------------------------------

    def handle_post(self, path: str, form: dict[str, str]) -> Response:
        store = form.get("store", "")
        actor = form.get("actor", "").strip()
        conn = self.connect()
        try:
            return self._act(conn, path, form, store, actor)
        except grocery.GroceryError as exc:
            return redirect("/", store=store, actor=actor, error=str(exc))
        finally:
            conn.close()

    def _act(
        self,
        conn: sqlite3.Connection,
        path: str,
        form: dict[str, str],
        store: str,
        actor: str,
    ) -> Response:
        actor_arg = actor or None

        if path == "/store":
            # Creating a store is the one action that needs an explicit name.
            row = grocery.store_row(conn, store, create=True)
            conn.commit()
            return redirect("/", store=row["name"], actor=actor,
                            notice=f"Store ready: {row['name']}")

        if path == "/layout":
            chosen = form.get("layout", "")
            if chosen not in sections.LAYOUTS:
                raise grocery.GroceryError(f"unknown layout: {chosen}")
            target = grocery.store_row(conn, store)
            conn.execute("UPDATE stores SET layout = ? WHERE id = ?",
                         (chosen, target["id"]))
            conn.commit()
            return redirect("/", store=target["name"], actor=actor,
                            notice=f"Walk order: {chosen}")

        if path not in {"/add", "/buy", "/unbuy", "/remove", "/close"}:
            return Response(
                404,
                page("Not found", f"<h1>404</h1><p>No route for {e(path)}.</p>"
                                  '<p><a href="/">Back to the list</a></p>'),
            )

        # Same fallback the CLI uses: last store touched, then the only store,
        # then GROCERY_DEFAULT_STORE. Raises when there is nothing to guess.
        store, store_assumption = grocery.resolve_store(conn, store or None)

        if path == "/add":
            items = parse_item_lines(form.get("items", ""))
            result = grocery.ingest_items(
                conn, store, items, "text",
                source_ref="webtest",
                raw_text=form.get("items", ""),
                actor=actor_arg,
            )
            parts = []
            if result["added"]:
                parts.append("added " + ", ".join(result["added"]))
            if result["merged"]:
                parts.append("merged " + ", ".join(result["merged"]))
            return redirect("/", store=result["store"], actor=actor,
                            notice="; ".join(parts) or "nothing to do",
                            assumed=join_assumptions(store_assumption))

        if path in {"/buy", "/unbuy"}:
            status = "purchased" if path == "/buy" else "needed"
            # A row's own unit pins the exact row. The free-text "mark bought"
            # form sends no unit at all, which is the assume path: the engine
            # picks a row (or creates one, for a buy) and says what it did.
            unit = form.get("unit")
            result = grocery.set_status(
                conn, store, [form.get("name", "")], status,
                grocery.unit_filter(unit), actor_arg,
            )
            verb = "bought" if status == "purchased" else "back on the list"
            return redirect(
                "/", store=store, actor=actor,
                notice=f"{', '.join(result['items'])}: {verb}",
                assumed=join_assumptions(
                    store_assumption, *result.get("assumptions", [])
                ),
            )

        if path == "/remove":
            # Deliberately no assume here: deletion has no undo, so an
            # ambiguous name stays a GroceryError the tester settles.
            removed = grocery.remove_items(
                conn, store, [form.get("name", "")],
                grocery.unit_filter(form.get("unit")), actor_arg,
            )
            return redirect("/", store=store, actor=actor,
                            notice=f"removed {', '.join(removed)}",
                            assumed=join_assumptions(store_assumption))

        result = grocery.close_trip(conn, store, actor=actor_arg)
        notice = (
            f"closed trip {result['trip_id']} at {result['store']}: "
            f"{len(result['purchased'])} purchased, "
            f"{len(result['carried_forward'])} carried forward"
        )
        return redirect("/", store=result["store"], actor=actor, notice=notice,
                        assumed=join_assumptions(store_assumption))


# --------------------------------------------------------------------------
# http plumbing
# --------------------------------------------------------------------------

class GroceryHandler(BaseHTTPRequestHandler):
    server_version = "grocery-webtest/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def app(self) -> GroceryWebApp:
        return self.server.app  # type: ignore[attr-defined]

    def _send(self, response: Response) -> None:
        self.send_response(response.status)
        for key, value in response.headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(response.body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response.body)

    def _fail(self, exc: BaseException) -> None:
        traceback.print_exception(exc, file=sys.stderr)
        body = page(
            "Server error",
            "<h1>Server error</h1>"
            f"<p>{e(type(exc).__name__)}: {e(str(exc))}</p>"
            "<p>The traceback is on the server console. "
            '<a href="/">Back to the list</a></p>',
        )
        self._send(Response(500, body))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            response = self.app.handle_get(
                parsed.path, flatten(parse_qs(parsed.query, keep_blank_values=True))
            )
        except Exception as exc:  # noqa: BLE001 - never leak a raw traceback
            self._fail(exc)
            return
        self._send(response)

    do_HEAD = do_GET

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY:
            self._send(Response(413, page("Too large", "<h1>413</h1><p>Body too large.</p>")))
            return
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        form = flatten(parse_qs(raw, keep_blank_values=True))
        try:
            response = self.app.handle_post(urlparse(self.path).path, form)
        except Exception as exc:  # noqa: BLE001 - never leak a raw traceback
            self._fail(exc)
            return
        self._send(response)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def make_server(db_path: str | Path, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """Build a server bound to 127.0.0.1. Port 0 picks a free port (tests)."""
    httpd = ThreadingHTTPServer((HOST, port), GroceryHandler)
    httpd.daemon_threads = True
    httpd.app = GroceryWebApp(db_path)  # type: ignore[attr-defined]
    return httpd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--db",
        default=str(grocery.DEV_DB),
        help="SQLite database path; defaults to the dev database, not the live list",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="TCP port on 127.0.0.1")
    args = parser.parse_args(argv)

    grocery.connect(args.db).close()  # create the schema before the first request
    httpd = make_server(args.db, args.port)
    host, port = httpd.server_address[:2]
    # flush: the banner must show even when stdout is a pipe or a log file.
    print(f"grocery web test → http://{host}:{port}/", flush=True)
    resolved = Path(args.db).expanduser().resolve()
    if resolved == Path(grocery.DEFAULT_DB).expanduser().resolve():
        print(f"database: {resolved}  ** LIVE LIST **", flush=True)
    else:
        print(f"database: {resolved}  (dev)", flush=True)
    print("localhost only; ctrl-c to stop", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
