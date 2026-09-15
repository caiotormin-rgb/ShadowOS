"""Synthetic messages only. Real mail must never enter this repository."""
from __future__ import annotations

import sqlite3

from mailctx.store import Message

DAY = 86400
T0 = 1_750_000_000  # fixed epoch so tests are deterministic


def msg(mid: str, *, thread: str | None = None, ts: int = T0, subject: str = "Subject",
        from_addr: str = "alice@example.invalid", to: str = "caio@example.invalid",
        snippet: str = "", labels=("INBOX",), attachments=(), history_id: int | None = None,
        cc: str = "", reply_to: str = "") -> Message:
    return Message(
        message_id=mid,
        thread_id=thread or f"t-{mid}",
        internal_ts=ts,
        from_addr=from_addr,
        to_addrs=to,
        cc_addrs=cc,
        reply_to=reply_to,
        subject=subject,
        snippet=snippet,
        labels=tuple(labels),
        attachments=tuple(attachments),
        history_id=history_id,
    )


def memory_conn():
    """A connection configured exactly like mailctx.store.connect().

    isolation_level=None matters: with Python's default, sqlite3 opens implicit
    transactions and an explicit BEGIN then fails. Tests that do not mirror
    production connection settings test the wrong thing.
    """
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
