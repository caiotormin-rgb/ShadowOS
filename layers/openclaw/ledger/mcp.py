"""MCP stdio server exposing the ledger to the OpenClaw chat agent.

Answers the questions Caio actually asks weekly: what did I pay X, what am I
subscribed to, when was that appointment, what were the travel dates.

Same shape as mailctx.mcp: JSON-RPC over stdio, stdlib only, read-only.

Two properties worth keeping:
  - Opened read-only. The agent reports; it never edits the ledger.
  - Queries run against the deduplicated `ledger` view, so "what did I pay
    Acme Lawn" returns 19 invoices and the true total rather than 68 raw rows and
    nearly three times the money. Reading raw rows is the single easiest way to be wrong here.
"""
from __future__ import annotations

import json, os, sqlite3, sys

NAME, VERSION = "ledger", "1.0.0"
DB = os.environ.get("LEDGER_DB",
                    os.path.expanduser("~/.local/state/ledger/ledger.sqlite"))
MAX_ROWS = 100

TOOLS = [
 {"name": "ledger_entity",
  "description": "Everything about one counterparty: totals, date range, and recent "
                 "transactions. Use for 'what did I pay Acme Lawn', 'how much have I "
                 "spent at Amazon', 'when did I last hear from Dana'.",
  "inputSchema": {"type": "object", "properties": {
      "name": {"type": "string", "description": "merchant or person, partial match"},
      "kind": {"type": "string", "description": "optional: purchase, subscription, booking, appointment, payment, bill, shipment"},
      "since": {"type": "string", "description": "optional ISO date"},
      "limit": {"type": "integer", "default": 25}},
   "required": ["name"]}},
 {"name": "ledger_search",
  "description": "Find transactions across all counterparties by kind and date. "
                 "Use for 'what subscriptions am I on', 'what did I buy in March', "
                 "'show me recent bookings'.",
  "inputSchema": {"type": "object", "properties": {
      "kind": {"type": "string"}, "since": {"type": "string"}, "until": {"type": "string"},
      "min_amount": {"type": "number"}, "limit": {"type": "integer", "default": 25}}}},
 {"name": "ledger_entities",
  "description": "Look up counterparties by name, or list the busiest. Use to resolve "
                 "a half-remembered name before asking for detail.",
  "inputSchema": {"type": "object", "properties": {
      "name": {"type": "string"}, "kind": {"type": "string", "description": "merchant | person"},
      "limit": {"type": "integer", "default": 20}}}},
 {"name": "ledger_status",
  "description": "What the ledger holds and how current it is.",
  "inputSchema": {"type": "object", "properties": {}}},
]


def conn():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def rows(cur, limit=MAX_ROWS):
    cur.row_factory = sqlite3.Row
    return [dict(r) for r in cur.fetchmany(limit)]


def money(c, where, args):
    r = c.execute(f"SELECT count(*) n, sum(amount) tot, min(date) f, max(date) l "
                  f"FROM ledger WHERE {where}", args).fetchone()
    return {"transactions": r[0], "total": round(r[1], 2) if r[1] else None,
            "first": r[2], "last": r[3]}


def call(name, a):
    if not os.path.exists(DB):
        return {"error": f"ledger not found at {DB}"}
    c = conn(); c.row_factory = sqlite3.Row
    lim = min(int(a.get("limit", 25)), MAX_ROWS)

    if name == "ledger_entity":
        where, args = ["(entity LIKE ? OR counterparty LIKE ?)"], [f"%{a['name']}%"] * 2
        if a.get("kind"):  where.append("kind = ?");  args.append(a["kind"])
        if a.get("since"): where.append("date >= ?"); args.append(a["since"])
        w = " AND ".join(where)
        summary = money(c, w, args)
        if not summary["transactions"]:
            near = [r["name"] for r in c.execute(
                "SELECT name FROM entities WHERE name LIKE ? LIMIT 5", (f"%{a['name']}%",))]
            return {"error": f"no transactions for {a['name']!r}",
                    "did_you_mean": near or None}
        summary["by_kind"] = {r["kind"]: r["n"] for r in c.execute(
            f"SELECT kind, count(*) n FROM ledger WHERE {w} GROUP BY kind", args)}
        summary["transactions_list"] = rows(c.execute(
            f"""SELECT date, counterparty, kind, amount, currency, ref_number,
                       service_dates, description, message_id
                  FROM ledger WHERE {w} ORDER BY date DESC LIMIT ?""", (*args, lim)), lim)
        return summary

    if name == "ledger_search":
        where, args = ["1=1"], []
        for k, op, col in (("kind", "=", "kind"), ("since", ">=", "date"), ("until", "<=", "date")):
            if a.get(k): where.append(f"{col} {op} ?"); args.append(a[k])
        if a.get("min_amount") is not None:
            where.append("amount >= ?"); args.append(float(a["min_amount"]))
        w = " AND ".join(where)
        out = money(c, w, args)
        out["by_entity"] = {r["entity"]: r["n"] for r in c.execute(
            f"SELECT entity, count(*) n FROM ledger WHERE {w} GROUP BY entity "
            f"ORDER BY n DESC LIMIT 20", args)}
        out["transactions_list"] = rows(c.execute(
            f"""SELECT date, entity, counterparty, kind, amount, currency, ref_number,
                       service_dates FROM ledger WHERE {w} ORDER BY date DESC LIMIT ?""",
            (*args, lim)), lim)
        return out

    if name == "ledger_entities":
        where, args = ["1=1"], []
        if a.get("name"): where.append("name LIKE ?"); args.append(f"%{a['name']}%")
        if a.get("kind"): where.append("kind = ?");     args.append(a["kind"])
        return {"entities": rows(c.execute(
            f"""SELECT name, kind, domain, message_count, first_seen, last_seen
                  FROM entities WHERE {' AND '.join(where)}
                 ORDER BY message_count DESC LIMIT ?""", (*args, lim)), lim)}

    if name == "ledger_status":
        q = lambda s: c.execute(s).fetchone()[0]
        return {"transactions": q("SELECT count(*) FROM ledger"),
                "entities": q("SELECT count(*) FROM entities"),
                "date_range": [q("SELECT min(date) FROM ledger"), q("SELECT max(date) FROM ledger")],
                "by_kind": {r["kind"]: r["n"] for r in c.execute(
                    "SELECT kind, count(*) n FROM ledger GROUP BY kind ORDER BY n DESC")},
                "with_amount": q("SELECT count(*) FROM ledger WHERE amount IS NOT NULL"),
                "last_run": q("SELECT max(finished_at) FROM extraction_runs")}

    return {"error": f"unknown tool: {name}"}


def handle(req):
    rid, m = req.get("id"), req.get("method")
    if m == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": NAME, "version": VERSION}}}
    if m == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if m == "tools/call":
        p = req.get("params", {})
        try:
            res = call(p.get("name", ""), p.get("arguments") or {})
        except Exception as e:
            res = {"error": type(e).__name__}          # never leak a traceback
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "content": [{"type": "text", "text": json.dumps(res, indent=1, ensure_ascii=False, default=str)}]}}
    return None if rid is None else {"jsonrpc": "2.0", "id": rid,
                                     "error": {"code": -32601, "message": "method not found"}}


def serve(stdin=None, stdout=None):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        resp = handle(req)
        if resp is not None:
            stdout.write(json.dumps(resp, default=str) + "\n"); stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(serve())
