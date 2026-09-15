"""SQLite storage for the mail-context read model.

Every write is idempotent so a replayed Gmail history page cannot corrupt the
index, and the sync cursor is advanced only by the caller after its transaction
commits.
"""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from . import SCHEMA_VERSION

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


@dataclass(frozen=True)
class Message:
    """One Gmail message, metadata only. There is deliberately no body field."""

    message_id: str
    thread_id: str
    internal_ts: int
    from_addr: str = ""
    to_addrs: str = ""
    cc_addrs: str = ""
    reply_to: str = ""
    subject: str = ""
    snippet: str = ""
    history_id: int | None = None
    labels: Sequence[str] = field(default_factory=tuple)
    attachments: Sequence[tuple[str, str, int | None]] = field(default_factory=tuple)


def connect(db_path: str | os.PathLike[str], *, read_only: bool = False) -> sqlite3.Connection:
    """Open the index. read_only=True is the agent-facing mode and is enforced
    by SQLite itself, not by convention."""
    path = Path(db_path)
    if read_only:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        existed = path.exists()
        conn = sqlite3.connect(path, isolation_level=None)
        if not existed:
            path.chmod(0o600)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if read_only:
        # Give the connection the derived attachment views before locking it.
        # Databases at schema v3 already have them; production and every
        # snapshot taken from it predate the migration, and a snapshot opened
        # mode=ro can never be given them permanently. TEMP objects shadow main
        # by name and live in a separate, writable database -- but `query_only`
        # blocks creating even those, so this has to happen first.
        from . import attachments
        attachments.ensure_views(conn)
        conn.execute("PRAGMA query_only = ON")
    else:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Apply the schema. Safe to run on every start."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"index schema v{current} is newer than this code (v{SCHEMA_VERSION}); refusing to touch it"
        )
    conn.executescript(SCHEMA_PATH.read_text())

    # v1 -> v2: mail_sync_state.initial_complete. ALTER is needed because
    # CREATE TABLE IF NOT EXISTS is a no-op on an existing table.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(mail_sync_state)")}
    if "initial_complete" not in cols:
        conn.execute("ALTER TABLE mail_sync_state "
                     "ADD COLUMN initial_complete INTEGER NOT NULL DEFAULT 0")

    # v2 -> v3: drop mail_messages.has_attachments. The column was fed by the
    # MIME walk in sync.parse_message, which never fires under format=metadata,
    # so it read 0 for every row in a 132,588-message index while genuinely
    # meaning "we never looked". Leaving it in place would leave a boolean that
    # answers a question nobody can answer, so it goes; presence is derived by
    # the mail_message_attachments view instead. Dropping is safe here: no
    # index, trigger or view references the column (the FTS triggers name only
    # subject, snippet and the address columns).
    cols = {r[1] for r in conn.execute("PRAGMA table_info(mail_messages)")}
    if "has_attachments" in cols:
        conn.execute("ALTER TABLE mail_messages DROP COLUMN has_attachments")

    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return SCHEMA_VERSION


class Store:
    """Write side of the index. Callers own transaction boundaries."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # -- transactions ----------------------------------------------------
    def begin(self) -> None:
        self.conn.execute("BEGIN IMMEDIATE")

    def commit(self) -> None:
        self.conn.execute("COMMIT")

    def rollback(self) -> None:
        self.conn.execute("ROLLBACK")

    def abort(self) -> bool:
        """Roll back only if a transaction is actually open.

        Error handlers need to record a failure, which means opening their own
        transaction -- but they may be reached with one already open, and
        BEGIN inside a transaction is an error. Returns whether it rolled back.
        """
        if self.conn.in_transaction:
            self.conn.execute("ROLLBACK")
            return True
        return False

    # -- messages --------------------------------------------------------
    def upsert_message(self, msg: Message, *, now: int | None = None) -> str:
        """Insert or refresh one message. Returns 'added' or 'updated'.

        Re-applying the same message is a no-op beyond synced_at, which is what
        makes replayed history pages safe.
        """
        now = int(time.time()) if now is None else now
        existed = self.conn.execute(
            "SELECT 1 FROM mail_messages WHERE message_id = ?", (msg.message_id,)
        ).fetchone() is not None

        self.conn.execute(
            """INSERT INTO mail_threads (thread_id, subject, participants, last_message_ts,
                                         message_count, synced_at)
               VALUES (?, ?, ?, ?, 0, ?)
               ON CONFLICT(thread_id) DO UPDATE SET
                 subject         = COALESCE(mail_threads.subject, excluded.subject),
                 last_message_ts = MAX(COALESCE(mail_threads.last_message_ts, 0), excluded.last_message_ts),
                 synced_at       = excluded.synced_at""",
            (msg.thread_id, msg.subject, msg.from_addr, msg.internal_ts, now),
        )
        self.conn.execute(
            """INSERT INTO mail_messages (message_id, thread_id, internal_ts, synced_at, history_id,
                                          from_addr, to_addrs, cc_addrs, reply_to, subject, snippet,
                                          deleted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
               ON CONFLICT(message_id) DO UPDATE SET
                 thread_id       = excluded.thread_id,
                 internal_ts     = excluded.internal_ts,
                 synced_at       = excluded.synced_at,
                 history_id      = MAX(COALESCE(mail_messages.history_id, 0), COALESCE(excluded.history_id, 0)),
                 from_addr       = excluded.from_addr,
                 to_addrs        = excluded.to_addrs,
                 cc_addrs        = excluded.cc_addrs,
                 reply_to        = excluded.reply_to,
                 subject         = excluded.subject,
                 snippet         = excluded.snippet,
                 deleted_at      = NULL""",
            (msg.message_id, msg.thread_id, msg.internal_ts, now, msg.history_id,
             msg.from_addr, msg.to_addrs, msg.cc_addrs, msg.reply_to, msg.subject,
             msg.snippet),
        )
        self.set_labels(msg.message_id, msg.labels)
        self.conn.execute("DELETE FROM mail_attachments WHERE message_id = ?", (msg.message_id,))
        for filename, mime_type, size in msg.attachments:
            self.conn.execute(
                """INSERT OR REPLACE INTO mail_attachments (message_id, filename, mime_type, size_bytes)
                   VALUES (?, ?, ?, ?)""",
                (msg.message_id, filename, mime_type, size),
            )
        self._refresh_thread(msg.thread_id)
        return "updated" if existed else "added"

    def existing_ids(self, ids: Sequence[str]) -> set[str]:
        """Which of these are already indexed and live.

        This is what makes a multi-hour initial load resumable: re-running skips
        everything already stored instead of re-fetching the whole mailbox.
        """
        if not ids:
            return set()
        out: set[str] = set()
        CHUNK = 900  # under SQLite's default 999 variable limit
        for i in range(0, len(ids), CHUNK):
            chunk = ids[i:i + CHUNK]
            q = ",".join("?" * len(chunk))
            out.update(r[0] for r in self.conn.execute(
                f"SELECT message_id FROM mail_messages WHERE deleted_at IS NULL "
                f"AND message_id IN ({q})", chunk))
        return out

    def set_labels(self, message_id: str, labels: Iterable[str]) -> None:
        labels = sorted(set(labels))
        self.conn.execute("DELETE FROM mail_labels WHERE message_id = ?", (message_id,))
        self.conn.executemany(
            "INSERT INTO mail_labels (message_id, label) VALUES (?, ?)",
            [(message_id, label) for label in labels],
        )

    def tombstone_message(self, message_id: str, *, now: int | None = None) -> bool:
        """Mark a message deleted in Gmail. Kept briefly for reconciliation."""
        now = int(time.time()) if now is None else now
        cur = self.conn.execute(
            "UPDATE mail_messages SET deleted_at = ? WHERE message_id = ? AND deleted_at IS NULL",
            (now, message_id),
        )
        if cur.rowcount:
            row = self.conn.execute(
                "SELECT thread_id FROM mail_messages WHERE message_id = ?", (message_id,)
            ).fetchone()
            if row:
                self._refresh_thread(row["thread_id"])
        return bool(cur.rowcount)

    def _refresh_thread(self, thread_id: str) -> None:
        self.conn.execute(
            """UPDATE mail_threads SET
                 message_count = (SELECT COUNT(*) FROM mail_messages
                                   WHERE thread_id = :tid AND deleted_at IS NULL),
                 last_message_ts = (SELECT MAX(internal_ts) FROM mail_messages
                                     WHERE thread_id = :tid AND deleted_at IS NULL),
                 participants = (SELECT GROUP_CONCAT(DISTINCT from_addr) FROM mail_messages
                                  WHERE thread_id = :tid AND deleted_at IS NULL)
               WHERE thread_id = :tid""",
            {"tid": thread_id},
        )

    # -- retention -------------------------------------------------------
    def prune(self, *, window_start_ts: int, tombstone_grace: int = 7 * 86400,
              now: int | None = None) -> int:
        """Drop messages outside the rolling window and expired tombstones."""
        now = int(time.time()) if now is None else now
        cur = self.conn.execute(
            """DELETE FROM mail_messages
                WHERE internal_ts < ?
                   OR (deleted_at IS NOT NULL AND deleted_at < ?)""",
            (window_start_ts, now - tombstone_grace),
        )
        pruned = cur.rowcount
        self.conn.execute(
            """DELETE FROM mail_threads WHERE thread_id NOT IN
               (SELECT DISTINCT thread_id FROM mail_messages)"""
        )
        return pruned

    # -- sync bookkeeping ------------------------------------------------
    def start_run(self, kind: str, *, now: int | None = None) -> int:
        now = int(time.time()) if now is None else now
        cur = self.conn.execute(
            "INSERT INTO mail_sync_runs (kind, started_at) VALUES (?, ?)", (kind, now)
        )
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, *, status: str, added: int = 0, updated: int = 0,
                   deleted: int = 0, pruned: int = 0, error_class: str | None = None,
                   now: int | None = None) -> None:
        now = int(time.time()) if now is None else now
        self.conn.execute(
            """UPDATE mail_sync_runs SET finished_at = ?, status = ?, added = ?, updated = ?,
                   deleted = ?, pruned = ?, error_class = ? WHERE run_id = ?""",
            (now, status, added, updated, deleted, pruned, error_class, run_id),
        )

    def advance_cursor(self, history_id: int, *, kind: str = "incremental",
                       now: int | None = None) -> None:
        """Move the sync cursor forward. Call ONLY after the data transaction
        for that history page has committed."""
        now = int(time.time()) if now is None else now
        full = (", last_full_sync_at = :now, initial_complete = 1"
                if kind in ("initial", "resync") else "")
        self.conn.execute(
            f"""UPDATE mail_sync_state SET last_history_id = :hid, last_success_at = :now,
                    status = 'ok', error_class = NULL{full} WHERE id = 1""",
            {"hid": history_id, "now": now},
        )

    def record_failure(self, error_class: str) -> None:
        self.conn.execute(
            "UPDATE mail_sync_state SET status = 'error', error_class = ? WHERE id = 1",
            (error_class,),
        )

    def set_window_start(self, window_start_ts: int) -> None:
        self.conn.execute(
            "UPDATE mail_sync_state SET window_start_ts = ? WHERE id = 1", (window_start_ts,)
        )
