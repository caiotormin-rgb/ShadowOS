"""Attachment targeting: which messages are worth fetching into the artifact
store.

Sourced from Gmail's own search index (`/users/me/messages?q=...`), which is
already on the read-only allowlist. That is a deliberate choice over reading
message payloads: format=metadata carries no parts, and format=full would pull
bodies into the metadata layer and break the no-body rule.

Each query returns ids only. Nothing about attachment *content* -- not a
filename, not a byte -- enters this layer; a hint says "this message carries
something of this shape", which is all targeting needs.
"""
from __future__ import annotations

import json
import time
from typing import Iterable

# hint -> Gmail search fragment. Order matters only for readability.
HINT_QUERIES: dict[str, str] = {
    "any": "has:attachment",
    "pdf": "has:attachment filename:pdf",
    "doc": "has:attachment (filename:doc OR filename:docx)",
    "sheet": "has:attachment (filename:xls OR filename:xlsx OR filename:csv)",
    "image": "has:attachment (filename:jpg OR filename:jpeg OR filename:png)",
    "large": "has:attachment larger:100000",
}

# Decorative attachments dominate ordinary mail (logos, signature graphics,
# tracking pixels). A message is a document candidate when it carries a
# document-shaped attachment, not merely "an attachment".
DOCUMENT_HINTS = ("pdf", "doc", "sheet")


def window_query(window_start_ts: int | None) -> str:
    if not window_start_ts:
        return ""
    return " after:" + time.strftime("%Y/%m/%d", time.gmtime(window_start_ts))


# Gmail's `after:` takes a date in the account's timezone; we derive that date
# from a UTC timestamp and truncate to the day. Either edge can slip. A run must
# not claim coverage it does not have, because "covered and not listed" is what
# licenses the answer 'no' -- so the floor we record is padded past the doubt.
GUARD_SECONDS = 2 * 86400


def covered_from(window_start_ts: int | None) -> int | None:
    """The oldest internal_ts a run with this window may speak for."""
    return None if not window_start_ts else window_start_ts + GUARD_SECONDS


def refresh_hints(api, conn, *, window_start_ts: int | None = None,
                  hints: Iterable[str] | None = None, now: int | None = None) -> dict[str, int]:
    """Populate mail_attachment_hints. Returns {hint: rows_written}.

    Only ids already present in mail_messages are stored -- the hint table is
    a view over the indexed window, never a way to smuggle in messages the
    sync has not seen.

    Every call also writes a row to `mail_targeting_runs`, which is what makes
    the absence of a hint readable. Before that table existed, an empty
    mail_attachment_hints meant either "never asked" or "asked, found nothing",
    and callers took it for the second.
    """
    now = int(time.time()) if now is None else now
    suffix = window_query(window_start_ts)
    requested = list(hints or HINT_QUERIES)

    # The run row is opened BEFORE the first API call and left at status
    # 'running'. A run that dies mid-way therefore leaves a record saying so,
    # and never counts as coverage -- half a sweep must not be allowed to turn
    # 'unknown' into 'no' for the half it never reached.
    cur = conn.execute(
        """INSERT INTO mail_targeting_runs
             (started_at, window_start_ts, covered_from_ts, covers_presence,
              hints, status)
           VALUES (?, ?, ?, ?, ?, 'running')""",
        (now, window_start_ts, covered_from(window_start_ts),
         1 if "any" in requested else 0, json.dumps(requested)))
    run_id = cur.lastrowid
    conn.commit()

    written: dict[str, int] = {}
    try:
        for hint in requested:
            q = HINT_QUERIES[hint] + suffix
            ids: list[str] = []
            token = None
            while True:
                page = api.list_message_ids(query=q, page_token=token)
                ids.extend(m["id"] for m in page.get("messages", []) or [])
                token = page.get("nextPageToken")
                if not token:
                    break
            rows = [(mid, hint, "gmail-query", now) for mid in ids]
            cur = conn.executemany(
                """INSERT OR REPLACE INTO mail_attachment_hints
                     (message_id, hint, source, synced_at)
                   SELECT ?, ?, ?, ? WHERE EXISTS
                     (SELECT 1 FROM mail_messages WHERE message_id = ?)""",
                [(m, h, s, t, m) for (m, h, s, t) in rows])
            written[hint] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            conn.commit()
    except Exception as exc:
        conn.execute(
            "UPDATE mail_targeting_runs SET status='failed', finished_at=?,"
            " error_class=?, rows_written=? WHERE run_id=?",
            (int(time.time()), type(exc).__name__, sum(written.values()), run_id))
        conn.commit()
        raise

    # The coverage ceiling is in *ingestion* time, and it is the run's START,
    # not its end: a message the sync writes while this run is walking Gmail may
    # already be past the page we fetched, so it stays 'unknown' until the next
    # run. Erring later would silently answer 'no' for it.
    conn.execute(
        "UPDATE mail_targeting_runs SET status='ok', finished_at=?, rows_written=?"
        " WHERE run_id=?",
        (now, sum(written.values()), run_id))
    conn.commit()
    return written


def document_candidates(conn, *, limit: int = 200, exclude_classes: Iterable[str] = ()) -> list[dict]:
    """Messages carrying a document-shaped attachment, newest first."""
    marks = ",".join("?" * len(DOCUMENT_HINTS))
    rows = conn.execute(
        f"""SELECT m.message_id, m.internal_ts, m.from_addr, m.subject,
                   group_concat(DISTINCT h.hint) AS hints
              FROM mail_attachment_hints h
              JOIN mail_messages m ON m.message_id = h.message_id
             WHERE h.hint IN ({marks}) AND m.deleted_at IS NULL
             GROUP BY m.message_id
             ORDER BY m.internal_ts DESC
             LIMIT ?""", (*DOCUMENT_HINTS, limit)).fetchall()
    return [dict(r) for r in rows]
