"""MCP stdio server exposing the read-only mail context to the OpenClaw agent.

This file is the agent boundary. Everything the chat agent can ever do with the
mail index is one of the four tools below -- there is no SQL parameter, no
database path, no filesystem handle, and no write operation of any kind.

Transport is newline-delimited JSON-RPC 2.0 on stdin/stdout, which is what MCP
stdio specifies. Nothing is imported that is not in the standard library.

Register it with:
    openclaw mcp add --name mail-context -- python3 -m mailctx.mcp
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

from .query import MAX_LIMIT, MailContext

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "mail-context", "version": "1.0.0"}

DB_PATH = Path.home() / ".local/state/mail-context/mail-context.sqlite"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "mail_search",
        "description": (
            "Search indexed Gmail metadata by keyword. Returns message subjects, "
            "participants, snippets, labels, and attachment filenames -- never message "
            "bodies or attachment contents, which are not stored. Every response reports "
            "how fresh the index is; treat a stale result as possibly missing recent mail. "
            "Each message carries attachment_state: 'yes', 'no', or 'unknown'. 'unknown' "
            "means nobody has ever asked Gmail whether that message has attachments -- it "
            "is NOT 'no', and must not be reported to the user as 'no attachments'. Call "
            "mail_status to see how much of the index attachment targeting covers."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free text matched against subject, snippet, and participants."},
                "sender": {"type": "string", "description": "Optional substring filter on the From address."},
                "since": {"type": "integer", "description": "Optional Unix timestamp; only messages at or after this."},
                "labels": {"type": "array", "items": {"type": "string"},
                           "description": "Optional Gmail label IDs; a message must carry ALL of them."},
                "limit": {"type": "integer", "description": f"Max results, 1-{MAX_LIMIT}.", "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "mail_thread",
        "description": "Return every indexed message in one Gmail thread, oldest first. Metadata only.",
        "inputSchema": {
            "type": "object",
            "properties": {"thread_id": {"type": "string", "description": "Gmail thread ID, as returned by mail_search."}},
            "required": ["thread_id"],
        },
    },
    {
        "name": "mail_candidates",
        "description": (
            "List detected action candidates (replies, appointments, bills, reminders, "
            "errands). Read-only: this reports what was detected and never performs an action."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["open", "accepted", "dismissed", "snoozed", "needs_review"]},
                "category": {"type": "string", "enum": ["reply", "appointment", "bill", "reminder", "errand"]},
                "limit": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "mail_status",
        "description": (
            "Report index size, last sync time, staleness, and how much of the index "
            "attachment targeting has covered. Call this when a search returns nothing "
            "surprising, to distinguish 'no such mail' from 'not synced yet' -- and "
            "before answering any question about attachments, since presence is unknown "
            "for every message until targeting has run."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


class ToolError(Exception):
    """A tool failed in a way the agent should be told about verbatim."""


def _ctx() -> MailContext:
    if not DB_PATH.exists():
        raise ToolError("The mail index has not been created yet. No mail is available to search.")
    return MailContext(DB_PATH)


def _call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    with _ctx() as ctx:
        if name == "mail_search":
            q = args.get("query")
            if not isinstance(q, str):
                raise ToolError("`query` is required and must be a string.")
            res = ctx.search(q, sender=args.get("sender"), since=args.get("since"),
                             labels=args.get("labels"), limit=int(args.get("limit", 20)))
        elif name == "mail_thread":
            tid = args.get("thread_id")
            if not isinstance(tid, str) or not tid:
                raise ToolError("`thread_id` is required and must be a string.")
            res = ctx.thread(tid)
        elif name == "mail_candidates":
            res = ctx.candidates(status=args.get("status"), category=args.get("category"),
                                 limit=int(args.get("limit", 50)))
        elif name == "mail_status":
            res = ctx.status()
        else:
            raise ToolError(f"Unknown tool: {name}")
        return res.to_dict()


# -- JSON-RPC plumbing ----------------------------------------------------
def _result(rid: Any, payload: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": payload}


def _error(rid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle(msg: dict) -> dict | None:
    """One request in, one response out. None means 'notification, stay silent'."""
    method = msg.get("method")
    rid = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        # Echo the client's protocol version when we can speak it, which is what
        # keeps this working against a newer OpenClaw without a code change.
        asked = params.get("protocolVersion")
        return _result(rid, {
            "protocolVersion": asked if isinstance(asked, str) and asked else PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })

    if method in ("notifications/initialized", "initialized"):
        return None

    if method == "ping":
        return _result(rid, {})

    if method == "tools/list":
        return _result(rid, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        try:
            payload = _call(name, args)
        except ToolError as exc:
            # isError keeps the agent informed without killing the session.
            return _result(rid, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
        except Exception as exc:
            return _result(rid, {"content": [{"type": "text",
                                              "text": f"mail-context failed: {exc.__class__.__name__}"}],
                                 "isError": True})
        return _result(rid, {"content": [{"type": "text",
                                          "text": json.dumps(payload, indent=2, default=str)}]})

    if rid is None:
        return None  # unknown notification
    return _error(rid, -32601, f"Method not found: {method}")


def serve(stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            stdout.write(json.dumps(_error(None, -32700, "Parse error")) + "\n")
            stdout.flush()
            continue
        response = handle(msg)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
