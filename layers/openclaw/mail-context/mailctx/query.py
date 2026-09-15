"""The read-only query contract exposed to the agent.

The agent gets these four methods and nothing else -- no SQL string, no
database path, no filesystem handle. The connection is opened `mode=ro` with
`query_only=ON`, so the restriction is enforced by SQLite rather than by
politeness.

Every response carries freshness. Stale context is allowed to be returned, but
never without saying how old it is.
"""
from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from . import attachments
from .store import connect

MAX_LIMIT = 100
_FTS_TOKEN = re.compile(r"[^\w@.\-]+", re.UNICODE)


@dataclass(frozen=True)
class Freshness:
    last_success_at: int | None
    age_seconds: int | None
    status: str
    error_class: str | None
    is_stale: bool
    window_start_ts: int | None

    def describe(self) -> str:
        if self.last_success_at is None:
            return "never synchronized -- results are empty or from a partial load"
        hours = (self.age_seconds or 0) / 3600
        base = f"last synced {hours:.1f}h ago"
        if self.status != "ok":
            base += f" (last run failed: {self.error_class or 'unknown'})"
        if self.is_stale:
            base += " -- STALE, may be missing recent mail"
        return base


@dataclass(frozen=True)
class Result:
    rows: list[dict[str, Any]]
    freshness: Freshness
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "freshness": {**asdict(self.freshness), "description": self.freshness.describe()},
            "truncated": self.truncated,
        }


def _fts_query(raw: str) -> str:
    """Turn free text into a safe FTS5 expression.

    Email content is untrusted, so tokens are stripped of FTS operators and
    re-quoted rather than passed through. A user cannot inject `OR`, `NEAR`, or
    a column filter this way.
    """
    tokens = [t for t in _FTS_TOKEN.split(raw or "") if t]
    if not tokens:
        return ""
    return " ".join('"' + t.replace('"', '""') + '"' for t in tokens)


class MailContext:
    """Read-only surface. Construct once per query batch."""

    STALE_AFTER = 36 * 3600  # twice-daily sync; ~1.5 missed cycles is stale

    def __init__(self, db_path: str | Path, *, now: int | None = None):
        self._conn: sqlite3.Connection = connect(db_path, read_only=True)
        self._now = now
        # Read once per instance, not once per row: it is a fixed fact for the
        # life of a query batch, and the budget is 100 ms for the whole call.
        self._attachment_coverage = attachments.coverage(self._conn)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "MailContext":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- freshness -------------------------------------------------------
    def _freshness(self) -> Freshness:
        row = self._conn.execute(
            """SELECT last_history_id, last_success_at, status, error_class, window_start_ts
                 FROM mail_sync_state WHERE id = 1"""
        ).fetchone()
        now = int(time.time()) if self._now is None else self._now
        last = row["last_success_at"] if row else None
        age = (now - last) if last else None
        return Freshness(
            last_success_at=last,
            age_seconds=age,
            status=(row["status"] if row else "never_run"),
            error_class=(row["error_class"] if row else None),
            is_stale=(age is None or age > self.STALE_AFTER),
            window_start_ts=(row["window_start_ts"] if row else None),
        )

    # -- contract --------------------------------------------------------
    def search(self, query: str, *, sender: str | None = None, since: int | None = None,
               labels: list[str] | None = None, limit: int = 20) -> Result:
        limit = max(1, min(int(limit), MAX_LIMIT))
        where = ["m.deleted_at IS NULL"]
        params: list[Any] = []

        match = _fts_query(query)
        if match:
            sql = ("SELECT m.* FROM mail_search s JOIN mail_messages m ON m.rowid = s.rowid "
                   "WHERE mail_search MATCH ? ")
            params.append(match)
        else:
            sql = "SELECT m.* FROM mail_messages m WHERE 1=1 "

        if sender:
            where.append("m.from_addr LIKE ?")
            params.append(f"%{sender}%")
        if since is not None:
            where.append("m.internal_ts >= ?")
            params.append(int(since))
        if labels:
            placeholders = ",".join("?" * len(labels))
            where.append(
                f"m.message_id IN (SELECT message_id FROM mail_labels WHERE label IN ({placeholders}) "
                f"GROUP BY message_id HAVING COUNT(DISTINCT label) = ?)"
            )
            params.extend(labels)
            params.append(len(labels))

        sql += " AND " + " AND ".join(where)
        sql += " ORDER BY m.internal_ts DESC LIMIT ?"
        params.append(limit + 1)

        rows = [self._row(r) for r in self._conn.execute(sql, params).fetchall()]
        truncated = len(rows) > limit
        return Result(rows[:limit], self._freshness(), truncated)

    def thread(self, thread_id: str) -> Result:
        rows = [
            self._row(r)
            for r in self._conn.execute(
                """SELECT * FROM mail_messages WHERE thread_id = ? AND deleted_at IS NULL
                    ORDER BY internal_ts ASC""",
                (thread_id,),
            ).fetchall()
        ]
        return Result(rows, self._freshness())

    def candidates(self, *, status: str | None = None, category: str | None = None,
                   limit: int = 50) -> Result:
        limit = max(1, min(int(limit), MAX_LIMIT))
        sql = ["SELECT c.*, m.subject AS source_subject, m.from_addr AS source_from",
               "FROM action_candidates c JOIN mail_messages m ON m.message_id = c.source_message_id",
               "WHERE 1=1"]
        params: list[Any] = []
        if status:
            sql.append("AND c.status = ?")
            params.append(status)
        if category:
            sql.append("AND c.category = ?")
            params.append(category)
        sql.append("ORDER BY c.updated_at DESC LIMIT ?")
        params.append(limit)
        rows = [dict(r) for r in self._conn.execute(" ".join(sql), params).fetchall()]
        return Result(rows, self._freshness())

    def status(self) -> Result:
        counts = self._conn.execute(
            """SELECT (SELECT COUNT(*) FROM mail_messages WHERE deleted_at IS NULL) AS messages,
                      (SELECT COUNT(*) FROM mail_threads)                           AS threads,
                      (SELECT COUNT(*) FROM action_candidates WHERE status='open')  AS open_candidates,
                      (SELECT COUNT(*) FROM draft_links WHERE state='active')       AS active_drafts"""
        ).fetchone()
        last_run = self._conn.execute(
            """SELECT kind, started_at, finished_at, added, updated, deleted, pruned, status, error_class
                 FROM mail_sync_runs ORDER BY started_at DESC LIMIT 1"""
        ).fetchone()
        row = dict(counts)
        row["last_run"] = dict(last_run) if last_run else None
        cov = self._attachment_coverage
        row["attachments"] = {
            "targeting_runs": cov.runs,
            "last_targeting_run_at": cov.last_run_at,
            "covered_from_ts": cov.covered_from_ts if cov.ever_ran else None,
            "description": cov.describe(),
        }
        return Result([row], self._freshness())

    # -- helpers ---------------------------------------------------------
    def _row(self, r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d.pop("rowid", None)
        # Defence in depth: a database that has not been migrated to schema v3
        # still carries the all-zero has_attachments column, and SELECT m.* would
        # hand it straight to the agent. It never leaves this layer.
        d.pop("has_attachments", None)
        mid = d["message_id"]
        d["labels"] = [
            x["label"] for x in self._conn.execute(
                "SELECT label FROM mail_labels WHERE message_id = ? ORDER BY label", (mid,)
            )
        ]
        d["attachments"] = [
            {"filename": x["filename"], "mime_type": x["mime_type"], "size_bytes": x["size_bytes"]}
            for x in self._conn.execute(
                "SELECT filename, mime_type, size_bytes FROM mail_attachments WHERE message_id = ?",
                (mid,),
            )
        ]
        # Three-valued, never a bare boolean. `attachments` being empty is not
        # evidence of anything: the sync fetches format=metadata and so never
        # sees a MIME part. See mailctx.attachments.
        hinted = self._conn.execute(
            "SELECT 1 FROM mail_attachment_hints WHERE message_id = ? LIMIT 1", (mid,)
        ).fetchone() is not None
        d["attachment_state"] = self._attachment_coverage.state_for(
            has_evidence=hinted or bool(d["attachments"]),
            internal_ts=d["internal_ts"], synced_at=d["synced_at"])
        return d
