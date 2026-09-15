"""The read-only query contract exposed to the agent.

The agent gets these five methods and nothing else -- no SQL string, no database
path, no filesystem handle, and no method whose name is `open`, `download`,
`export` or `content`. The connection is opened `mode=ro` with `query_only=ON`,
so the restriction is enforced by SQLite rather than by politeness.

Every response carries freshness. Stale context is allowed to be returned, but
never without saying how old it is.

File and folder names are untrusted input. A folder can be named
`ignore previous instructions`; a file can be named `" OR 1=1` or `NEAR(x y)`.
Names are therefore data in every direction: stripped of FTS operators before
they reach SQLite, and returned as row values that carry no instruction weight.
"""
from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from mailctx.preflight import WAIVER_PATH

from .store import connect

MAX_LIMIT = 100

# Path resolution bounds. Drive's parent graph is a DAG in principle and an
# untrusted graph in practice, so every one of these is a real ceiling rather
# than a tidy default.
MAX_PATH_DEPTH = 32
MAX_PATHS = 8
MAX_PATH_STEPS = MAX_PATHS * MAX_PATH_DEPTH * 4

# How a path ended. 'my_drive' and 'orphan' are complete answers; the rest are
# honest admissions.
ROOT_KINDS = ("my_drive", "orphan", "unresolved_parent", "cycle", "depth_limit", "not_indexed")

_PREFIX = {
    "my_drive": "/",
    "orphan": "(orphan)/",
    "unresolved_parent": "(unresolved)/",
    "cycle": "(cycle)/",
    "depth_limit": "(depth-limit)/",
    "not_indexed": "",
}

_FTS_TOKEN = re.compile(r"[^\w@.\-]+", re.UNICODE)


@dataclass(frozen=True)
class Freshness:
    """Same shape as mailctx.query.Freshness, minus the rolling-window field
    that mail has and Drive does not."""

    last_success_at: int | None
    age_seconds: int | None
    status: str
    error_class: str | None
    is_stale: bool

    def describe(self) -> str:
        if self.last_success_at is None:
            return "never synchronized -- results are empty or from a partial load"
        hours = (self.age_seconds or 0) / 3600
        base = f"last synced {hours:.1f}h ago"
        if self.status != "ok":
            base += f" (last run failed: {self.error_class or 'unknown'})"
        if self.is_stale:
            base += " -- STALE, may be missing recent changes"
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

    File names are untrusted, so tokens are stripped of FTS operators and
    re-quoted rather than passed through. A caller -- or a file name -- cannot
    inject `OR`, `NEAR`, or a column filter this way.
    """
    tokens = [t for t in _FTS_TOKEN.split(raw or "") if t]
    if not tokens:
        return ""
    return " ".join('"' + t.replace('"', '""') + '"' for t in tokens)


class DriveContext:
    """Read-only surface. Construct once per query batch."""

    # Four-times-daily timer means a six-hour cycle; three missed cycles is
    # stale. Derived from the timer, not picked.
    STALE_AFTER = 18 * 3600

    def __init__(self, db_path: str | Path, *, now: int | None = None,
                 waiver_path: Path = WAIVER_PATH):
        self._conn: sqlite3.Connection = connect(db_path, read_only=True)
        self._now = now
        self._waiver_path = waiver_path

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "DriveContext":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- freshness -------------------------------------------------------
    def _freshness(self) -> Freshness:
        row = self._conn.execute(
            """SELECT last_success_at, status, error_class
                 FROM drive_sync_state WHERE id = 1"""
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
        )

    # -- contract --------------------------------------------------------
    def search(self, query: str, *, mime_type: str | None = None, owner: str | None = None,
               modified_since: int | None = None, include_trashed: bool = False,
               limit: int = 20) -> Result:
        """Name search with metadata filters.

        Trashed items are excluded by default because "where does that document
        live" almost never means the trash, but they remain indexed and are
        returned by `file()` and `path()` and by `include_trashed=True`.
        """
        limit = max(1, min(int(limit), MAX_LIMIT))
        where = ["f.deleted_at IS NULL"]
        params: list[Any] = []

        match = _fts_query(query)
        if match:
            sql = ("SELECT f.* FROM drive_search s JOIN drive_files f ON f.rowid = s.rowid "
                   "WHERE drive_search MATCH ? ")
            params.append(match)
        else:
            sql = "SELECT f.* FROM drive_files f WHERE 1=1 "

        if not include_trashed:
            where.append("f.trashed = 0")
        if mime_type:
            # Accept either a real MIME type or the derived kind, so a caller
            # does not have to know Google's magic folder MIME string.
            where.append("(f.mime_type = ? OR f.kind = ?)")
            params.extend([mime_type, mime_type])
        if owner:
            where.append(
                "f.file_id IN (SELECT file_id FROM drive_owners "
                "WHERE email_address LIKE ? OR display_name LIKE ?)")
            params.extend([f"%{owner}%", f"%{owner}%"])
        if modified_since is not None:
            where.append("f.modified_ts >= ?")
            params.append(int(modified_since))

        sql += " AND " + " AND ".join(where)
        sql += " ORDER BY f.modified_ts DESC LIMIT ?"
        params.append(limit + 1)

        rows = [self._row(r) for r in self._conn.execute(sql, params).fetchall()]
        truncated = len(rows) > limit
        return Result(rows[:limit], self._freshness(), truncated)

    def file(self, file_id: str) -> Result:
        row = self._conn.execute(
            "SELECT * FROM drive_files WHERE file_id = ?", (file_id,)).fetchone()
        return Result([self._row(row)] if row else [], self._freshness())

    def path(self, file_id: str) -> Result:
        """Every path this file has, resolved upward through the parent DAG.

        Returns all of them. A multi-parent file genuinely has more than one
        location and collapsing them to the first would be a lie about the data.
        Traversal is bounded in depth and breadth, tracks visited ids per path so
        a cycle terminates instead of looping, and ends a branch at an explicit
        marker rather than guessing when a parent is not in the index.
        """
        fresh = self._freshness()
        node = self._node(file_id)
        if node is None:
            return Result([self._path_row([], "not_indexed")], fresh)

        root_id = self._root_folder_id()
        results: list[dict[str, Any]] = []
        truncated = False
        steps = 0
        # Each stack entry is one partial path: the id being expanded, the
        # segments collected so far (file first, upward), and the ids already on
        # this path -- per path, not global, because two different paths may
        # legitimately share a folder.
        stack: list[tuple[str, list[dict], set[str]]] = [(file_id, [node], {file_id})]

        while stack:
            if len(results) >= MAX_PATHS or steps >= MAX_PATH_STEPS:
                truncated = True
                break
            steps += 1
            current, chain, visited = stack.pop()
            parents = [r[0] for r in self._conn.execute(
                "SELECT parent_id FROM drive_parents WHERE file_id = ? ORDER BY parent_id",
                (current,))]

            if not parents:
                results.append(self._path_row(
                    chain, "my_drive" if current == root_id else "orphan"))
                continue

            for parent_id in parents:
                if parent_id in visited:
                    results.append(self._path_row(chain, "cycle"))
                    continue
                if len(chain) >= MAX_PATH_DEPTH:
                    results.append(self._path_row(chain, "depth_limit"))
                    continue
                if parent_id == root_id:
                    node_row = self._node(parent_id)
                    results.append(self._path_row(
                        chain + ([node_row] if node_row else []), "my_drive"))
                    continue
                parent = self._node(parent_id)
                if parent is None:
                    results.append(self._path_row(
                        chain + [{"file_id": parent_id, "name": None, "kind": None,
                                  "unresolved": True}],
                        "unresolved_parent"))
                    continue
                stack.append((parent_id, chain + [parent], visited | {parent_id}))

        return Result(results[:MAX_PATHS], fresh, truncated or len(results) > MAX_PATHS)

    def recent(self, *, limit: int = 20, include_trashed: bool = False) -> Result:
        limit = max(1, min(int(limit), MAX_LIMIT))
        sql = ("SELECT * FROM drive_files WHERE deleted_at IS NULL "
               + ("" if include_trashed else "AND trashed = 0 ")
               + "ORDER BY modified_ts DESC LIMIT ?")
        rows = [self._row(r) for r in self._conn.execute(sql, (limit + 1,)).fetchall()]
        return Result(rows[:limit], self._freshness(), len(rows) > limit)

    def status(self) -> Result:
        counts = self._conn.execute(
            """SELECT (SELECT COUNT(*) FROM drive_files WHERE deleted_at IS NULL)            AS files,
                      (SELECT COUNT(*) FROM drive_files WHERE deleted_at IS NULL
                                                          AND kind = 'folder')               AS folders,
                      (SELECT COUNT(*) FROM drive_files WHERE deleted_at IS NULL
                                                          AND trashed = 1)                   AS trashed,
                      (SELECT COUNT(*) FROM drive_files WHERE deleted_at IS NOT NULL)        AS tombstoned,
                      (SELECT COUNT(*) FROM drive_parents)                                   AS parent_edges"""
        ).fetchone()
        exposure = {
            r["sharing_state"]: r["n"] for r in self._conn.execute(
                """SELECT sharing_state, COUNT(*) AS n FROM drive_files
                    WHERE deleted_at IS NULL GROUP BY sharing_state""")
        }
        last_run = self._conn.execute(
            """SELECT kind, started_at, finished_at, added, updated, deleted, pruned,
                      excluded, status, error_class
                 FROM drive_sync_runs ORDER BY started_at DESC, run_id DESC LIMIT 1"""
        ).fetchone()
        row = dict(counts)
        sync_state = self._conn.execute(
            "SELECT initial_complete FROM drive_sync_state WHERE id = 1").fetchone()
        # A partial index is a different answer to "is this everything", and the
        # consumer is entitled to know which one it is holding.
        row["initial_complete"] = bool(sync_state["initial_complete"]) if sync_state else False
        row["sharing"] = {state: exposure.get(state, 0) for state in
                          ("private", "shared_with_named", "domain", "anyone_with_link", "unknown")}
        row["last_run"] = dict(last_run) if last_run else None
        # The preflight waiver is surfaced here so an unencrypted index cannot be
        # quietly forgotten. Only its existence and first line are read.
        row["encryption_waiver"] = self._waiver_note()
        return Result([row], self._freshness())

    # -- helpers ---------------------------------------------------------
    def _waiver_note(self) -> str | None:
        try:
            if not self._waiver_path.exists():
                return None
            first = self._waiver_path.read_text().strip().splitlines()
            return (first[0][:120] if first else "no reason recorded")
        except OSError:
            return None

    def _root_folder_id(self) -> str | None:
        row = self._conn.execute(
            "SELECT root_folder_id FROM drive_sync_state WHERE id = 1").fetchone()
        return row["root_folder_id"] if row else None

    def _node(self, file_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT file_id, name, kind FROM drive_files WHERE file_id = ?", (file_id,)
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _path_row(chain: list[dict], root: str) -> dict[str, Any]:
        """One resolved path, top-down. `root` says how it ended and `complete`
        says whether that ending is an answer or an admission."""
        segments = list(reversed(chain))
        names = [s.get("name") or "?" for s in segments]
        return {
            "segments": segments,
            "display": _PREFIX[root] + "/".join(names),
            "root": root,
            "complete": root in ("my_drive", "orphan"),
        }

    def _row(self, r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d.pop("rowid", None)
        file_id = d["file_id"]
        d["parents"] = [x[0] for x in self._conn.execute(
            "SELECT parent_id FROM drive_parents WHERE file_id = ? ORDER BY parent_id", (file_id,))]
        d["owners"] = [
            {"display_name": x["display_name"], "email_address": x["email_address"]}
            for x in self._conn.execute(
                "SELECT display_name, email_address FROM drive_owners WHERE file_id = ? "
                "ORDER BY email_address", (file_id,))
        ]
        # Sharing is a state plus counts. There is no grantee list to return
        # because there is no grantee list to store.
        d["sharing"] = {
            "state": d.pop("sharing_state"),
            "named_user_count": d.pop("named_user_count"),
            "group_count": d.pop("group_count"),
            "link_discoverable": bool(d.pop("link_discoverable")),
            "max_role": d.pop("max_role"),
        }
        return d
