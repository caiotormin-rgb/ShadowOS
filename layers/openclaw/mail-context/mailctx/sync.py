"""Synchronization: bounded initial load, then incremental history.

Two invariants carry the whole design:

* The cursor is captured *before* the initial listing and advanced *after* the
  data transaction commits. Capturing it afterwards would silently drop every
  change that landed during the load, and advancing it first would drop a page
  on any crash.
* Every write is idempotent, so an interrupted run is resumed by simply running
  it again -- there is no repair path to get wrong.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .gmail import GmailReadOnly, TransportError
from .store import Message, Store

# Where the index lives, for every entry point that needs it. Defined here
# rather than in syncrun because three CLIs already imported it from this
# module and got an ImportError -- one of them being the targeting run an
# operator handoff tells a human to type.
DB_PATH = Path.home() / ".local/state/mail-context/mail-context.sqlite"

# Gmail's history endpoint only retains ~30 days. Past that the cursor is dead
# and a bounded resync is the only correct recovery.
HISTORY_HORIZON_DAYS = 30

# Operator chose 24 months on 2026-08-23, widening the plan's original 12.
# Measured cost of the wider window: 64,192 messages vs 32,041, ~63 MiB vs ~32,
# and 0.03% of the daily quota. Lifetime (269,429) was considered and deferred:
# feasible on quota and disk, but a many-hour initial load for mail old enough
# that Gmail search is the better tool.
DEFAULT_WINDOW_DAYS = 730


@dataclass
class SyncResult:
    kind: str
    added: int = 0
    updated: int = 0
    deleted: int = 0
    pruned: int = 0
    pages: int = 0
    status: str = "ok"
    error_class: str | None = None
    elapsed: float = 0.0
    resynced: bool = False
    truncated: bool = False

    def summary(self) -> str:
        note = " TRUNCATED (initial load incomplete)" if self.truncated else ""
        return (f"{self.kind}: +{self.added} ~{self.updated} -{self.deleted} "
                f"pruned={self.pruned} pages={self.pages} in {self.elapsed:.1f}s "
                f"[{self.status}{'/' + self.error_class if self.error_class else ''}]{note}")


def parse_message(raw: dict) -> Message:
    """Gmail metadata JSON -> Message. Never reads a body.

    NOTE (2026-08-24): format=metadata returns headers ONLY -- no `parts`, not
    even part descriptors. The attachment walk below is therefore dead code
    against the live API and `mail_attachments` stayed empty through a full
    64k load. It is kept because it is correct for any caller that hands us a
    fuller payload; attachment *presence* is sourced separately, from Gmail's
    own search index -- see mailctx.targeting. Do not "fix" this by switching
    the sync to format=full: that would pull bodies into the metadata layer
    and break the no-body rule the schema and tests enforce."""
    headers = {h["name"].lower(): h["value"]
               for h in raw.get("payload", {}).get("headers", [])}
    attachments: list[tuple[str, str, int | None]] = []

    def walk(part: dict) -> None:
        filename = part.get("filename")
        if filename:
            attachments.append((filename, part.get("mimeType", ""),
                                part.get("body", {}).get("size")))
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(raw.get("payload", {}))

    internal = raw.get("internalDate")
    ts = int(internal) // 1000 if internal else 0

    return Message(
        message_id=raw["id"],
        thread_id=raw.get("threadId", raw["id"]),
        internal_ts=ts,
        from_addr=headers.get("from", ""),
        to_addrs=headers.get("to", ""),
        cc_addrs=headers.get("cc", ""),
        reply_to=headers.get("reply-to", ""),
        subject=headers.get("subject", ""),
        snippet=raw.get("snippet", ""),
        history_id=int(raw["historyId"]) if raw.get("historyId") else None,
        labels=tuple(raw.get("labelIds", []) or []),
        attachments=tuple(attachments),
    )


class Syncer:
    def __init__(self, store: Store, api: GmailReadOnly, *,
                 window_days: int | None = DEFAULT_WINDOW_DAYS,
                 exclude_query: str | None = None,
                 batch_size: int = 200,
                 concurrency: int = 8,
                 resume: bool = True,
                 on_progress: Callable[[str], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        """window_days=None indexes the full mailbox history with no age prune.

        concurrency exists because a metadata GET is latency-bound, not quota-
        bound: sequentially the full mailbox measures ~4.5 msg/s (16h). Gmail's
        ceiling is 50/s (250 quota units at 5 per call); a live run at 51 msg/s
        was refused with 403, so the default now sits below it and the transport
        throttles on top of that.
        """
        self.store = store
        self.api = api
        self.window_days = window_days
        # Extra Gmail search terms, ANDed into the *listing* query, so ids
        # that do not match are never returned and never fetched -- the
        # cheapest filter layer there is. Despite the field name, inclusions
        # work too ('in:sent'); the name is historical.
        self.exclude_query = exclude_query
        self.batch_size = batch_size
        self.concurrency = max(1, concurrency)
        self.resume = resume
        self._progress = on_progress or (lambda _msg: None)
        self._sleep = sleep
        self.skipped = 0

    # -- helpers ---------------------------------------------------------
    def window_start_ts(self, now: int | None = None) -> int:
        if self.window_days is None:
            return 0
        now = int(time.time()) if now is None else now
        return now - self.window_days * 86400

    def _query(self) -> str | None:
        parts = []
        if self.window_days is not None:
            parts.append(f"newer_than:{self.window_days}d")
        if self.exclude_query:
            parts.append(self.exclude_query)
        return " ".join(parts) if parts else None

    def _fetch_one(self, mid: str) -> Message | None:
        """One metadata GET, with the two failures that are normal rather than fatal."""
        for attempt in range(4):
            try:
                return parse_message(self.api.message_metadata(mid))
            except TransportError as exc:
                if exc.error_class == "not_found":
                    return None  # deleted between listing and fetching
                if exc.error_class == "rate_limited":
                    self._sleep(2.0 * (2 ** attempt))  # exponential backoff
                    continue
                raise
        raise TransportError("rate_limited_persistent")

    def _fetch_batch(self, ids: Sequence[str], *,
                     skip_existing: bool = False) -> tuple[list[Message], int]:
        """Fetch a batch concurrently. Writes stay on the calling thread, so the
        SQLite connection is never touched by a worker.

        skip_existing is for the initial load only. Incremental sync must never
        skip: history told us the message changed, and an already-present row is
        exactly the one whose labels are now stale.
        """
        if skip_existing and self.resume:
            already = self.store.existing_ids(list(ids))
            if already:
                self.skipped += len(already)
                ids = [i for i in ids if i not in already]
        if not ids:
            return [], 0
        if self.concurrency == 1:
            results = [self._fetch_one(i) for i in ids]
        else:
            with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                results = list(pool.map(self._fetch_one, ids))
        out = [m for m in results if m is not None]
        return out, len(results) - len(out)

    # -- initial ---------------------------------------------------------
    def initial_sync(self, *, max_messages: int | None = None) -> SyncResult:
        t0 = time.time()
        res = SyncResult(kind="initial")
        run_id = self.store.start_run("initial")

        try:
            # Cursor FIRST: anything that changes during the load is replayed by
            # the next incremental run rather than lost.
            cursor = int(self.api.profile()["historyId"])
            window = self.window_start_ts()
            self.store.begin()
            self.store.set_window_start(window)
            self.store.commit()

            token, seen, truncated = None, 0, False
            while True:
                page = self.api.list_message_ids(query=self._query(), page_token=token,
                                                 max_results=500)
                ids = [m["id"] for m in page.get("messages", [])]
                res.pages += 1

                for i in range(0, len(ids), self.batch_size):
                    batch = ids[i:i + self.batch_size]
                    messages, _missing = self._fetch_batch(batch, skip_existing=True)
                    self.store.begin()
                    for msg in messages:
                        if self.store.upsert_message(msg) == "added":
                            res.added += 1
                        else:
                            res.updated += 1
                    self.store.commit()
                    seen += len(batch)
                    if max_messages and seen >= max_messages:
                        truncated = True
                        token = None
                        break
                    self._progress(f"{seen:,} messages, page {res.pages}")

                if max_messages and seen >= max_messages:
                    break
                token = page.get("nextPageToken")
                if not token:
                    break

            res.truncated = truncated
            if truncated:
                # A bounded validation run is NOT a completed initial load.
                # Advancing the cursor here would make the next scheduled run
                # take the incremental branch, and the messages never fetched
                # would never be backfilled -- the index would look healthy at
                # 2,000 of 64,192 forever.
                self._progress("bounded run: cursor NOT advanced, load is incomplete")
            else:
                self.store.begin()
                self.store.advance_cursor(cursor, kind="initial")
                self.store.commit()

        except TransportError as exc:
            res.status, res.error_class = "error", exc.error_class
            self.store.abort()
            self.store.begin(); self.store.record_failure(exc.error_class); self.store.commit()
        except Exception:
            res.status, res.error_class = "error", "internal"
            self.store.abort()
            self.store.begin(); self.store.record_failure("internal"); self.store.commit()
            raise
        finally:
            res.elapsed = time.time() - t0
            self.store.finish_run(run_id, status=res.status, added=res.added,
                                  updated=res.updated, deleted=res.deleted,
                                  error_class=res.error_class)
        return res

    # -- incremental -----------------------------------------------------
    def incremental_sync(self) -> SyncResult:
        t0 = time.time()
        res = SyncResult(kind="incremental")
        run_id = self.store.start_run("incremental")
        state = self.store.conn.execute(
            "SELECT last_history_id FROM mail_sync_state WHERE id = 1").fetchone()
        start = state["last_history_id"] if state else None

        if not start:
            self.store.finish_run(run_id, status="skipped", error_class="no_cursor")
            res.status, res.error_class = "skipped", "no_cursor"
            return res

        try:
            newest = start
            token = None
            added_ids: set[str] = set()
            deleted_ids: set[str] = set()

            while True:
                page = self.api.history(start, page_token=token)
                res.pages += 1
                newest = max(newest, int(page.get("historyId", newest)))
                for record in page.get("history", []):
                    for item in record.get("messagesAdded", []):
                        added_ids.add(item["message"]["id"])
                    for item in record.get("messagesDeleted", []):
                        deleted_ids.add(item["message"]["id"])
                    for key in ("labelsAdded", "labelsRemoved"):
                        for item in record.get(key, []):
                            added_ids.add(item["message"]["id"])
                token = page.get("nextPageToken")
                if not token:
                    break

            added_ids -= deleted_ids  # a message added then deleted is just gone

            for i in range(0, len(sorted(added_ids)), self.batch_size):
                batch = sorted(added_ids)[i:i + self.batch_size]
                messages, _ = self._fetch_batch(batch)
                self.store.begin()
                for msg in messages:
                    if self.store.upsert_message(msg) == "added":
                        res.added += 1
                    else:
                        res.updated += 1
                self.store.commit()

            if deleted_ids:
                self.store.begin()
                for mid in deleted_ids:
                    if self.store.tombstone_message(mid):
                        res.deleted += 1
                self.store.commit()

            self.store.begin()
            res.pruned = self.store.prune(window_start_ts=self.window_start_ts())
            self.store.advance_cursor(newest)
            self.store.commit()

        except TransportError as exc:
            if exc.error_class == "not_found":
                # Cursor older than Gmail's history horizon. Documented recovery.
                self.store.abort()
                self.store.finish_run(run_id, status="resync", error_class="stale_cursor")
                # Mark `res`, not just the returned object: `return` inside
                # `except` still runs `finally`, which would otherwise call
                # finish_run a second time and overwrite the row just written,
                # making every stale-cursor recovery invisible in the run log.
                res.status = "resync"
                out = self.initial_sync()
                out.resynced = True
                out.elapsed = time.time() - t0
                return out
            res.status, res.error_class = "error", exc.error_class
            self.store.abort()
            self.store.begin(); self.store.record_failure(exc.error_class); self.store.commit()
        finally:
            res.elapsed = time.time() - t0
            if res.status != "resync":
                self.store.finish_run(run_id, status=res.status, added=res.added,
                                      updated=res.updated, deleted=res.deleted,
                                      pruned=res.pruned, error_class=res.error_class)
        return res

    def sync(self) -> SyncResult:
        """Entry point for the timer.

        Dispatch is on whether a full load ever *completed*, not on whether a
        cursor exists. A bounded or failed load leaves a cursor behind, and
        dispatching on that would take the incremental branch over a
        half-filled index and never backfill the remainder.
        """
        row = self.store.conn.execute(
            "SELECT last_history_id, initial_complete FROM mail_sync_state WHERE id = 1"
        ).fetchone()
        complete = bool(row and row["initial_complete"] and row["last_history_id"])
        return self.incremental_sync() if complete else self.initial_sync()
