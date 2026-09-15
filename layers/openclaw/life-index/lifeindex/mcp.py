"""MCP stdio server exposing the life-index catalog to the OpenClaw agent.

Mirrors mailctx.mcp: JSON-RPC over stdio, stdlib only, read-only.

Two deliberate limits:

  - The store is opened read-only. The agent can find and describe documents;
    it cannot catalog, retag, or delete them.
  - Document TEXT is returned only for tier 3 by default. Tier 1 and 2 are
    identity, tax, contract, and medical material, and this server is reached
    from chat apps -- so those return metadata, extracted fields, and a path,
    which is what "where is my lease" actually needs. LIFE_INDEX_AGENT_TEXT=all
    lifts it deliberately.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import paths
from .store import Catalog, connect

NAME = "life-index"
VERSION = "1.0.0"
TEXT_POLICY = os.environ.get("LIFE_INDEX_AGENT_TEXT", "tier3")
TEXT_SNIPPET = 2000

TOOLS = [
    {"name": "artifact_search",
     "description": "Search cataloged documents by content, title, or filename. "
                    "Returns matches with a highlighted excerpt.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "words to look for"},
         "doc_type": {"type": "string", "description": "optional: contract, tax, identity, education, medical, correspondence"},
         "tier": {"type": "integer", "description": "optional: 1, 2 or 3"},
         "limit": {"type": "integer", "default": 10}},
         "required": ["query"]}},
    {"name": "artifact_get",
     "description": "Full record for one document: metadata, tags, extracted fields, and its path.",
     "inputSchema": {"type": "object", "properties": {
         "sha256": {"type": "string", "description": "full or 12+ char prefix"}},
         "required": ["sha256"]}},
    {"name": "artifact_fields",
     "description": "Look up extracted values (amounts, CPF, matricula, dates) "
                    "across documents. Answers questions like 'what did we agree to pay'.",
     "inputSchema": {"type": "object", "properties": {
         "key": {"type": "string", "description": "e.g. amount_brl, cpf, matricula, date_br"},
         "doc_type": {"type": "string"},
         "limit": {"type": "integer", "default": 25}}}},
    {"name": "artifact_status",
     "description": "What the catalog holds: counts by tier and type, and whether the store is encrypted.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def _catalog() -> Catalog:
    return Catalog(connect(paths.DB, read_only=True))


def _redact(row: dict) -> dict:
    """Tier 1/2 documents return metadata and a path, not their text."""
    if TEXT_POLICY != "all" and (row.get("tier") or 3) < 3:
        row.pop("excerpt", None)
        row["text_withheld"] = ("tier %s document; open the path directly, or set "
                                "LIFE_INDEX_AGENT_TEXT=all" % row.get("tier"))
    return row


def call(name: str, args: dict) -> dict:
    if paths.mode() == "unavailable" or not paths.DB.exists():
        return {"error": "life-index store is not available on this machine"}
    cat = _catalog()
    if name == "artifact_search":
        rows = cat.search(args.get("query", ""), limit=int(args.get("limit", 10)))
        if args.get("doc_type"):
            rows = [r for r in rows if r.get("doc_type") == args["doc_type"]]
        if args.get("tier"):
            rows = [r for r in rows if r.get("tier") == int(args["tier"])]
        out = []
        for r in rows:
            out.append(_redact({k: r.get(k) for k in
                                ("sha256", "title", "original_name", "doc_type", "tier",
                                 "doc_date", "source", "excerpt", "needs_review")}))
        return {"count": len(out), "results": out}
    if name == "artifact_get":
        digest = args.get("sha256", "")
        row = cat.get(digest)
        if not row and len(digest) >= 8:
            hit = cat.conn.execute(
                "SELECT sha256 FROM artifacts WHERE sha256 LIKE ?", (digest + "%",)).fetchone()
            row = cat.get(hit["sha256"]) if hit else None
        if not row:
            return {"error": "not found"}
        row["path"] = str(cat.blob_path(row["sha256"]))
        if TEXT_POLICY == "all" or (row.get("tier") or 3) >= 3:
            t = cat.conn.execute(
                "SELECT text FROM artifact_text WHERE sha256=?", (row["sha256"],)).fetchone()
            if t:
                row["text"] = t["text"][:TEXT_SNIPPET]
        else:
            row["text_withheld"] = f"tier {row['tier']} document"
        return row
    if name == "artifact_fields":
        sql = ("SELECT f.key, f.value, a.sha256, a.title, a.doc_type, a.tier "
               "FROM artifact_fields f JOIN artifacts a ON a.sha256=f.sha256 WHERE 1=1")
        params: list = []
        if args.get("key"):
            sql += " AND f.key = ?"; params.append(args["key"])
        if args.get("doc_type"):
            sql += " AND a.doc_type = ?"; params.append(args["doc_type"])
        sql += " ORDER BY a.tier, f.key LIMIT ?"; params.append(int(args.get("limit", 25)))
        return {"fields": [dict(r) for r in cat.conn.execute(sql, params)]}
    if name == "artifact_status":
        q = lambda s: cat.conn.execute(s).fetchone()[0]
        return {"store_mode": paths.mode(),
                "encrypted": paths.mode() == "encrypted",
                "artifacts": q("SELECT count(*) FROM artifacts"),
                "by_tier": {str(r["tier"]): r["n"] for r in cat.conn.execute(
                    "SELECT tier, count(*) n FROM artifacts GROUP BY tier")},
                "by_type": {(r["doc_type"] or "untyped"): r["n"] for r in cat.conn.execute(
                    "SELECT doc_type, count(*) n FROM artifacts GROUP BY doc_type")},
                "needs_review": q("SELECT count(*) FROM artifacts WHERE needs_review=1")}
    return {"error": f"unknown tool: {name}"}


def handle(req: dict) -> dict | None:
    rid, method = req.get("id"), req.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": NAME, "version": VERSION}}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        p = req.get("params", {})
        try:
            result = call(p.get("name", ""), p.get("arguments") or {})
        except Exception as e:                       # never leak a stack trace
            result = {"error": f"{type(e).__name__}"}
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "content": [{"type": "text",
                         "text": json.dumps(result, indent=1, ensure_ascii=False)}]}}
    if rid is None:
        return None                                   # a notification
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": "method not found"}}


def serve(stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        resp = handle(req)
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(serve())
