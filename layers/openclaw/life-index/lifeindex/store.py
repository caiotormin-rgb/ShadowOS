"""Catalog storage. Content-addressed blobs, metadata filing."""
from __future__ import annotations

import hashlib
import mimetypes
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from . import paths


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def connect(db_path: Path | str, *, read_only: bool = False) -> sqlite3.Connection:
    uri = f"file:{db_path}" + ("?mode=ro" if read_only else "")
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not read_only:
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


# --- schema reconciliation --------------------------------------------------
#
# schema.sql is entirely CREATE ... IF NOT EXISTS, so it only ever builds a
# store from nothing. Add a column to a table that already exists on disk and
# the CREATE is skipped, the column never lands, and the next statement -- an
# index on that column -- dies with "no such column". apply_schema() runs on
# every write path, so the whole store goes unwritable rather than degrading.
# That happened on 2026-08-25 with correspondents.entity_key.
#
# A hand-maintained migration list does not fix this, because the failure mode
# *is* someone editing schema.sql and forgetting the second edit. So instead we
# diff what schema.sql declares against what the file actually has and ALTER in
# the difference. schema.sql stays the single source of truth.


class SchemaReconcileError(RuntimeError):
    """A declared column is missing from a live table and cannot be ALTERed in.

    Raised instead of letting the plain "no such column" surface later: this
    one names the table, the column, and why SQLite refused.
    """


_TABLE_CONSTRAINTS = frozenset(
    {"primary", "unique", "check", "foreign", "constraint"})


def _column_defs(ddl: str) -> list[str]:
    """The comma-separated items inside a CREATE TABLE body, comments removed.

    Comment-aware on purpose: schema.sql documents its columns inline, and
    those comments carry both commas and parens ("-- 0 deterministic, 1 local,
    3 cloud", "(Contrato - Alex e Sam, final.doc)"). A naive split on ',' lands
    in the middle of a sentence and produces garbage DDL.
    """
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    started = False
    i, n = 0, len(ddl)
    while i < n:
        ch = ddl[i]
        nxt = ddl[i + 1] if i + 1 < n else ""
        if ch == "-" and nxt == "-":                    # line comment
            j = ddl.find("\n", i)
            i = n if j < 0 else j
            continue
        if ch == "/" and nxt == "*":                    # block comment
            j = ddl.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if ch in "'\"":                                 # literal or quoted name
            j = i + 1
            while j < n:
                if ddl[j] == ch:
                    if j + 1 < n and ddl[j + 1] == ch:  # doubled = escaped
                        j += 2
                        continue
                    break
                j += 1
            if started:
                buf.append(ddl[i:j + 1])
            i = j + 1
            continue
        if ch == "(":
            depth += 1
            if depth == 1 and not started:              # opens the table body
                started = True
                i += 1
                continue
        elif ch == ")":
            depth -= 1
            if depth == 0 and started:                  # closes it
                break
        elif ch == "," and depth == 1:
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        if started:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return [" ".join(p.split()) for p in parts if p.strip()]


def _column_name(frag: str) -> str | None:
    """The column a definition fragment declares, or None for a table
    constraint such as UNIQUE (name, kind)."""
    tok = frag.split("(")[0].split()
    if not tok or tok[0].lower() in _TABLE_CONSTRAINTS:
        return None
    return tok[0].strip('"[]`')


def _mask_literals(frag: str) -> str:
    """Same string with quoted literals blanked, so scanning for keywords and
    counting parens is not thrown off by their contents. Length is preserved,
    so offsets into the mask are valid offsets into the original."""
    return re.sub(r"'(?:[^']|'')*'", lambda m: "x" * len(m.group()), frag)


def _strip_unique(frag: str) -> tuple[str, bool]:
    """Remove a top-level UNIQUE column constraint, reporting whether it was
    there.

    SQLite cannot ALTER a UNIQUE column into existence, so the column goes in
    plain and a separate unique index carries the constraint -- which is
    exactly what the live store was given by hand on 2026-08-25, under this
    same index name.
    """
    masked = _mask_literals(frag)
    for m in re.finditer(r"\bUNIQUE\b", masked, re.I):
        if masked.count("(", 0, m.start()) == masked.count(")", 0, m.start()):
            return " ".join((frag[:m.start()] + frag[m.end():]).split()), True
    return frag, False


def _declared(sql: str) -> dict[str, tuple[str, dict[str, tuple]]]:
    """What schema.sql declares: table -> (CREATE statement, {column: info}).

    Built by running the schema into an empty in-memory database and reading it
    back. SQLite isolates one statement per table for us and is the authority
    on what the script actually declares -- far steadier than parsing the whole
    file ourselves. The CREATE text is kept alongside because CHECK and
    REFERENCES clauses only survive verbatim in the text; table_info flattens
    them away.
    """
    ref = sqlite3.connect(":memory:")
    try:
        ref.executescript(sql)
        virtual: set[str] = set()
        tables: dict[str, str] = {}
        for name, ddl in ref.execute(
                "SELECT name, sql FROM sqlite_master"
                " WHERE type='table' AND sql IS NOT NULL"):
            if re.match(r"\s*CREATE\s+VIRTUAL\s+TABLE", ddl, re.I):
                virtual.add(name)
            else:
                tables[name] = ddl
        # FTS5 keeps shadow tables (artifact_search_data, _idx, _docsize...).
        # They are the extension's to manage, and their shape tracks its
        # version, not ours.
        return {n: (d, {r[1]: r for r in ref.execute(f"PRAGMA table_info({n})")})
                for n, d in tables.items()
                if not any(n.startswith(v + "_") for v in virtual)}
    finally:
        ref.close()


def reconcile_columns(conn: sqlite3.Connection, sql: str) -> list[str]:
    """ALTER in every column schema.sql declares that a live table is missing.

    Returns the DDL it applied, so callers and tests can see the difference.
    Idempotent -- a store that already matches yields an empty list. Only ever
    ADDs: no table is rewritten, existing rows are never touched, and in
    SQLite an ADD COLUMN is an O(1) metadata edit regardless of row count.

    Columns land at the end of the table, so a migrated store and a fresh one
    agree on names but not on ordinal position. Everything here reads columns
    by name (row_factory is sqlite3.Row throughout), which is what makes that
    safe.
    """
    applied: list[str] = []
    for table, (ddl, info) in _declared(sql).items():
        live = _columns(conn, table)
        if not live:
            continue          # not on disk at all; CREATE TABLE will build it
        for frag in _column_defs(ddl):
            col = _column_name(frag)
            if col is None or col in live:
                continue
            row = info.get(col)
            if row is None:
                continue
            notnull, dflt, pk = row[3], row[4], row[5]
            where = f"{table}.{col}"
            if pk:
                raise SchemaReconcileError(
                    f"{where} is declared PRIMARY KEY and the table already "
                    f"exists; SQLite cannot add a primary key. This needs a "
                    f"hand-written table rebuild.")
            if notnull and dflt is None:
                raise SchemaReconcileError(
                    f"{where} is declared NOT NULL with no DEFAULT and the "
                    f"table already exists; SQLite cannot add it. Give it a "
                    f"DEFAULT in schema.sql, or rebuild the table by hand.")
            if re.search(r"\bGENERATED\b|\bAS\s*\(", _mask_literals(frag), re.I):
                raise SchemaReconcileError(
                    f"{where} is a generated column; SQLite cannot add a "
                    f"STORED one. This needs a hand-written table rebuild.")
            body, unique = _strip_unique(frag)
            stmt = f"ALTER TABLE {table} ADD COLUMN {body}"
            conn.execute(stmt)
            applied.append(stmt)
            if unique:
                stmt = (f"CREATE UNIQUE INDEX IF NOT EXISTS {table}_{col}_uq"
                        f" ON {table}({col})")
                conn.execute(stmt)
                applied.append(stmt)
    if applied:
        conn.commit()
    return applied


def _columns(conn, table) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.OperationalError:
        return set()


def apply_schema(conn: sqlite3.Connection) -> None:
    """Bring a store up to schema.sql, whether it is empty or predates it."""
    sql = (Path(__file__).resolve().parent.parent / "schema.sql").read_text()
    reconcile_columns(conn, sql)
    conn.executescript(sql)
    conn.commit()
    # The FTS table is derived. If it predates the sha256 column, rebuild it —
    # otherwise search silently returns nothing for every artifact.
    if "sha256" not in _columns(conn, "artifact_search"):
        conn.execute("DROP TABLE IF EXISTS artifact_search")
        conn.executescript(sql)
        for r in conn.execute("""SELECT a.sha256, a.title, a.original_name,
                                        coalesce(t.text,'') AS text
                                   FROM artifacts a
                                   LEFT JOIN artifact_text t USING (sha256)"""):
            conn.execute("INSERT INTO artifact_search (sha256,title,original_name,text)"
                         " VALUES (?,?,?,?)", tuple(r))
        conn.commit()


@dataclass
class Artifact:
    sha256: str
    bytes: int
    mime: str | None
    ext: str
    source: str
    source_ref: str | None = None
    thread_id: str | None = None
    original_name: str | None = None
    title: str | None = None
    doc_type: str | None = None
    tier: int = 3
    doc_date: str | None = None
    correspondent: int | None = None
    needs_review: int = 0
    review_reason: str | None = None
    tags: Sequence[str] = field(default_factory=tuple)


class Catalog:
    def __init__(self, conn: sqlite3.Connection, blob_root: Path | None = None):
        self.conn = conn
        self.blob_root = Path(blob_root) if blob_root else paths.BLOBS

    # -- blobs -----------------------------------------------------------
    def blob_path(self, digest: str) -> Path:
        """Sharded by the first two hex chars: 64k files in one directory is
        slow to list and unpleasant to recover from."""
        return self.blob_root / digest[:2] / digest

    def store_blob(self, src: Path, digest: str, *, now: int | None = None) -> Path:
        dest = self.blob_path(digest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copy2(src, dest)
            dest.chmod(0o600)
        self.conn.execute(
            """INSERT OR REPLACE INTO artifact_blobs (sha256, rel_path, stored_at)
               VALUES (?,?,?)""",
            (digest, str(dest.relative_to(self.blob_root)), now or int(time.time())))
        return dest

    # -- artifacts -------------------------------------------------------
    def exists(self, digest: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM artifacts WHERE sha256 = ?", (digest,)).fetchone() is not None

    def upsert(self, a: Artifact, *, now: int | None = None) -> str:
        now = now or int(time.time())
        self.conn.execute(
            """INSERT INTO artifacts (sha256, bytes, mime, ext, source, source_ref,
                   thread_id, original_name, title, doc_type, tier, doc_date,
                   correspondent, needs_review, review_reason, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(sha256) DO UPDATE SET
                   title = coalesce(excluded.title, artifacts.title),
                   doc_type = coalesce(excluded.doc_type, artifacts.doc_type),
                   tier = excluded.tier,
                   doc_date = coalesce(excluded.doc_date, artifacts.doc_date),
                   needs_review = excluded.needs_review,
                   review_reason = excluded.review_reason,
                   updated_at = excluded.updated_at""",
            (a.sha256, a.bytes, a.mime, a.ext, a.source, a.source_ref, a.thread_id,
             a.original_name, a.title, a.doc_type, a.tier, a.doc_date,
             a.correspondent, a.needs_review, a.review_reason, now, now))
        self._index(a.sha256, a.title, a.original_name)
        for tag in a.tags:
            self.conn.execute(
                "INSERT OR IGNORE INTO artifact_tags (sha256, tag) VALUES (?,?)",
                (a.sha256, tag))
        self.conn.commit()
        return a.sha256

    def _index(self, digest: str, title: str | None, name: str | None,
               text: str | None = None) -> None:
        """Write this artifact's FTS row. Called on every upsert, so an
        artifact with no extractable text is still findable by title and
        filename — which is the whole point of a pointer artifact."""
        if text is None:
            row = self.conn.execute(
                "SELECT text FROM artifact_text WHERE sha256=?", (digest,)).fetchone()
            text = row["text"] if row else ""
        self.conn.execute("DELETE FROM artifact_search WHERE sha256 = ?", (digest,))
        self.conn.execute(
            "INSERT INTO artifact_search (sha256,title,original_name,text) VALUES (?,?,?,?)",
            (digest, title or "", name or "", text or ""))

    def set_text(self, digest: str, text: str, *, extractor: str,
                 pages: int | None = None, now: int | None = None) -> None:
        now = now or int(time.time())
        self.conn.execute(
            """INSERT OR REPLACE INTO artifact_text
                   (sha256, text, chars, pages, extractor, extracted_at)
               VALUES (?,?,?,?,?,?)""",
            (digest, text, len(text), pages, extractor, now))
        row = self.conn.execute(
            "SELECT title, original_name FROM artifacts WHERE sha256=?", (digest,)).fetchone()
        self._index(digest, row["title"] if row else "",
                    row["original_name"] if row else "", text)
        self.conn.commit()

    def add_fields(self, digest: str, pairs: Iterable[tuple[str, str]], *,
                   run_id: int | None = None, confidence: float | None = None) -> int:
        n = 0
        for key, value in pairs:
            self.conn.execute(
                """INSERT OR REPLACE INTO artifact_fields
                       (sha256, key, value, confidence, run_id) VALUES (?,?,?,?,?)""",
                (digest, key, value, confidence, run_id))
            n += 1
        self.conn.commit()
        return n

    def start_run(self, lane: int, model: str | None = None,
                  version: str | None = None) -> int:
        cur = self.conn.execute(
            """INSERT INTO extraction_runs (lane, model, version, started_at)
               VALUES (?,?,?,?)""", (lane, model, version, int(time.time())))
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, items: int) -> None:
        self.conn.execute(
            "UPDATE extraction_runs SET finished_at=?, items=? WHERE run_id=?",
            (int(time.time()), items, run_id))
        self.conn.commit()

    # -- retrieval -------------------------------------------------------
    SEARCH_SQL = """SELECT a.*, snippet(artifact_search, 3, '[', ']', '…', 12) AS excerpt
                      FROM artifact_search
                      JOIN artifacts a ON a.sha256 = artifact_search.sha256
                     WHERE artifact_search MATCH ?
                     ORDER BY rank LIMIT ?"""

    def search(self, query: str, *, limit: int = 20) -> list[dict]:
        """Full-text over title, filename and text. Every artifact has a row,
        so a metadata-only pointer is findable by name even with no content.

        Terms are quoted, so a stray * or OR is matched as a literal word.
        Matching widens: exact AND, then prefix AND, then prefix OR — FTS5
        does not stem, and this corpus is bilingual.
        """
        terms = [t for t in query.split() if t.strip()]
        if not terms:
            return []
        exact  = [f'"{t}"'  for t in terms]
        prefix = [f'"{t}"*' for t in terms]
        attempts = [" ".join(exact), " ".join(prefix)]
        if len(terms) > 1:
            attempts.append(" OR ".join(prefix))
        for expr in attempts:
            try:
                rows = self.conn.execute(self.SEARCH_SQL, (expr, limit)).fetchall()
            except sqlite3.OperationalError:
                continue
            if rows:
                return [dict(r) for r in rows]
        return []

    def get(self, digest: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM artifacts WHERE sha256 = ?", (digest,)).fetchone()
        if not row:
            return None
        out = dict(row)
        out["tags"] = [r["tag"] for r in self.conn.execute(
            "SELECT tag FROM artifact_tags WHERE sha256=? ORDER BY tag", (digest,))]
        out["fields"] = [dict(r) for r in self.conn.execute(
            "SELECT key, value, confidence FROM artifact_fields WHERE sha256=? ORDER BY key",
            (digest,))]
        return out
