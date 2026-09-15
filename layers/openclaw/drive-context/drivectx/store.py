"""SQLite storage for the drive-context read model.

Every write is idempotent so a replayed Changes page cannot corrupt the index,
and the sync cursor is advanced only by the caller after its transaction
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
from .drive import FOLDER_MIME, SHORTCUT_MIME
from .sharing import Sharing

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"

TOMBSTONE_GRACE = 7 * 86400  # long enough for reconciliation, short enough to forget


@dataclass(frozen=True)
class DriveFile:
    """One Drive file or folder, metadata only.

    There is deliberately no content, text, thumbnail or checksum field, and no
    grantee list -- sharing arrives already reduced to a `Sharing` summary.
    """

    file_id: str
    name: str = ""
    mime_type: str = ""
    created_ts: int | None = None
    modified_ts: int | None = None
    size_bytes: int | None = None
    trashed: bool = False
    explicitly_trashed: bool = False
    starred: bool = False
    owned_by_me: bool | None = None
    web_view_link: str = ""
    shortcut_target_id: str | None = None
    shortcut_target_mime: str | None = None
    parents: Sequence[str] = field(default_factory=tuple)
    owners: Sequence[tuple[str, str]] = field(default_factory=tuple)
    sharing: Sharing = field(default_factory=Sharing.unknown)

    @property
    def kind(self) -> str:
        if self.mime_type == FOLDER_MIME:
            return "folder"
        if self.mime_type == SHORTCUT_MIME:
            return "shortcut"
        return "file"


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
        transaction -- but they may be reached with one already open, and BEGIN
        inside a transaction is an error. Returns whether it rolled back.
        """
        if self.conn.in_transaction:
            self.conn.execute("ROLLBACK")
            return True
        return False

    # -- files -----------------------------------------------------------
    def upsert_file(self, f: DriveFile, *, now: int | None = None,
                    run_id: int | None = None) -> str:
        """Insert or refresh one file. Returns 'added' or 'updated'.

        Re-applying the same file is a no-op beyond synced_at, which is what
        makes a replayed Changes page safe. Re-applying it also clears any
        tombstone: Drive can restore a file, and the index must follow.
        """
        now = int(time.time()) if now is None else now
        existed = self.conn.execute(
            "SELECT 1 FROM drive_files WHERE file_id = ?", (f.file_id,)
        ).fetchone() is not None

        s = f.sharing
        self.conn.execute(
            """INSERT INTO drive_files (file_id, name, mime_type, kind, created_ts, modified_ts,
                    size_bytes, trashed, explicitly_trashed, starred, owned_by_me, web_view_link,
                    shortcut_target_id, shortcut_target_mime, sharing_state, named_user_count,
                    group_count, link_discoverable, max_role, synced_at, seen_run, deleted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
               ON CONFLICT(file_id) DO UPDATE SET
                 name                 = excluded.name,
                 mime_type            = excluded.mime_type,
                 kind                 = excluded.kind,
                 created_ts           = COALESCE(excluded.created_ts, drive_files.created_ts),
                 modified_ts          = excluded.modified_ts,
                 size_bytes           = excluded.size_bytes,
                 trashed              = excluded.trashed,
                 explicitly_trashed   = excluded.explicitly_trashed,
                 starred              = excluded.starred,
                 owned_by_me          = excluded.owned_by_me,
                 web_view_link        = excluded.web_view_link,
                 shortcut_target_id   = excluded.shortcut_target_id,
                 shortcut_target_mime = excluded.shortcut_target_mime,
                 sharing_state        = excluded.sharing_state,
                 named_user_count     = excluded.named_user_count,
                 group_count          = excluded.group_count,
                 link_discoverable    = excluded.link_discoverable,
                 max_role             = excluded.max_role,
                 synced_at            = excluded.synced_at,
                 seen_run             = COALESCE(excluded.seen_run, drive_files.seen_run),
                 deleted_at           = NULL""",
            (f.file_id, f.name, f.mime_type, f.kind, f.created_ts, f.modified_ts,
             f.size_bytes, int(f.trashed), int(f.explicitly_trashed), int(f.starred),
             None if f.owned_by_me is None else int(f.owned_by_me), f.web_view_link,
             f.shortcut_target_id, f.shortcut_target_mime, s.state, s.named_user_count,
             s.group_count, int(s.link_discoverable), s.max_role, now, run_id),
        )
        self.set_parents(f.file_id, f.parents)
        self.set_owners(f.file_id, f.owners)
        return "updated" if existed else "added"

    def existing_ids(self, ids: Sequence[str]) -> set[str]:
        """Which of these are already indexed and live.

        This is what makes a long initial load resumable: re-running skips
        everything already stored. It is used by the initial listing ONLY --
        on an incremental run the stored row is exactly the stale one.
        """
        if not ids:
            return set()
        out: set[str] = set()
        CHUNK = 900  # under SQLite's default 999 variable limit
        for i in range(0, len(ids), CHUNK):
            chunk = list(ids[i:i + CHUNK])
            q = ",".join("?" * len(chunk))
            out.update(r[0] for r in self.conn.execute(
                f"SELECT file_id FROM drive_files WHERE deleted_at IS NULL "
                f"AND file_id IN ({q})", chunk))
        return out

    def set_parents(self, file_id: str, parents: Iterable[str]) -> None:
        """Replace the edge set. A file moved out of a folder must lose that
        edge, so this applies rather than accumulates."""
        parents = sorted({p for p in parents if p})
        self.conn.execute("DELETE FROM drive_parents WHERE file_id = ?", (file_id,))
        self.conn.executemany(
            "INSERT INTO drive_parents (file_id, parent_id) VALUES (?, ?)",
            [(file_id, p) for p in parents],
        )

    def set_owners(self, file_id: str, owners: Iterable[tuple[str, str]]) -> None:
        seen: dict[str, str] = {}
        for display_name, email in owners:
            seen.setdefault(email or "", display_name or "")
        self.conn.execute("DELETE FROM drive_owners WHERE file_id = ?", (file_id,))
        self.conn.executemany(
            "INSERT INTO drive_owners (file_id, display_name, email_address) VALUES (?, ?, ?)",
            [(file_id, name, email) for email, name in sorted(seen.items())],
        )

    def tombstone_file(self, file_id: str, *, now: int | None = None) -> bool:
        """Mark a file removed in Drive. Kept briefly for reconciliation.

        This is not trashing. A trashed file is still indexed, still answerable
        and flagged as trashed; a removed one is gone from the account's view.
        """
        now = int(time.time()) if now is None else now
        cur = self.conn.execute(
            "UPDATE drive_files SET deleted_at = ? WHERE file_id = ? AND deleted_at IS NULL",
            (now, file_id),
        )
        return bool(cur.rowcount)

    # -- reconciliation --------------------------------------------------
    def sweep_missing(self, *, run_id: int, now: int | None = None) -> int:
        """Tombstone every live row that the given full listing did not touch.

        Only a *complete* listing may call this. A resync that stopped at its
        page budget has not seen the whole corpus, so anything it did not touch
        is unproven rather than gone -- and tombstoning on an unproven absence
        would delete the index one truncated run at a time.

        The marker is the run id, not a timestamp. Two runs in the same second
        share a timestamp and a clock can step backwards, either of which would
        make a sweep silently no-op or silently over-reach.
        """
        now = int(time.time()) if now is None else now
        cur = self.conn.execute(
            """UPDATE drive_files SET deleted_at = ?
                WHERE deleted_at IS NULL AND (seen_run IS NULL OR seen_run < ?)""",
            (now, run_id),
        )
        return int(cur.rowcount)

    def prune(self, *, tombstone_grace: int = TOMBSTONE_GRACE, now: int | None = None) -> int:
        """Drop expired tombstones.

        There is no rolling age window here, unlike mail-context: Drive metadata
        is small and a file from 2014 is exactly the one whose location the
        operator has forgotten.
        """
        now = int(time.time()) if now is None else now
        cur = self.conn.execute(
            "DELETE FROM drive_files WHERE deleted_at IS NOT NULL AND deleted_at < ?",
            (now - tombstone_grace,),
        )
        return int(cur.rowcount)

    # -- sync bookkeeping ------------------------------------------------
    def start_run(self, kind: str, *, now: int | None = None) -> int:
        now = int(time.time()) if now is None else now
        cur = self.conn.execute(
            "INSERT INTO drive_sync_runs (kind, started_at) VALUES (?, ?)", (kind, now)
        )
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, *, status: str, added: int = 0, updated: int = 0,
                   deleted: int = 0, pruned: int = 0, excluded: int = 0,
                   error_class: str | None = None, now: int | None = None) -> None:
        now = int(time.time()) if now is None else now
        self.conn.execute(
            """UPDATE drive_sync_runs SET finished_at = ?, status = ?, added = ?, updated = ?,
                   deleted = ?, pruned = ?, excluded = ?, error_class = ? WHERE run_id = ?""",
            (now, status, added, updated, deleted, pruned, excluded, error_class, run_id),
        )

    def cursor(self) -> str | None:
        row = self.conn.execute(
            "SELECT page_token FROM drive_sync_state WHERE id = 1").fetchone()
        return row["page_token"] if row else None

    def advance_cursor(self, page_token: str, *, kind: str = "incremental",
                       now: int | None = None) -> None:
        """Move the Changes cursor forward. Call ONLY after the data transaction
        for that page has committed."""
        now = int(time.time()) if now is None else now
        full = ", last_full_sync_at = :now" if kind in ("initial", "resync") else ""
        self.conn.execute(
            f"""UPDATE drive_sync_state SET page_token = :tok, last_success_at = :now,
                    status = 'ok', error_class = NULL{full} WHERE id = 1""",
            {"tok": page_token, "now": now},
        )

    def initial_complete(self) -> bool:
        """Whether a full listing ever finished.

        Deliberately not inferred from the cursor. The cursor is captured
        *before* the listing begins, so a bounded validation run or a run that
        died on page three both leave one behind -- and dispatching on it would
        strand every unlisted file forever behind incremental runs that only
        ever see what changed since.
        """
        row = self.conn.execute(
            "SELECT initial_complete FROM drive_sync_state WHERE id = 1").fetchone()
        return bool(row["initial_complete"]) if row else False

    def mark_initial_complete(self) -> None:
        self.conn.execute("UPDATE drive_sync_state SET initial_complete = 1 WHERE id = 1")

    def clear_cursor(self) -> None:
        """Forget an expired page token. The index itself is left alone: it is
        stale, not wrong, and a bounded resync is about to refresh it."""
        self.conn.execute("UPDATE drive_sync_state SET page_token = NULL WHERE id = 1")

    def record_failure(self, error_class: str) -> None:
        self.conn.execute(
            "UPDATE drive_sync_state SET status = 'error', error_class = ? WHERE id = 1",
            (error_class,),
        )

    def set_root_folder_id(self, file_id: str) -> None:
        self.conn.execute(
            "UPDATE drive_sync_state SET root_folder_id = ? WHERE id = 1", (file_id,)
        )

    def root_folder_id(self) -> str | None:
        row = self.conn.execute(
            "SELECT root_folder_id FROM drive_sync_state WHERE id = 1").fetchone()
        return row["root_folder_id"] if row else None
